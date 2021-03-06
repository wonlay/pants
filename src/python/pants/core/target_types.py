# Copyright 2020 Pants project contributors (see CONTRIBUTORS.md).
# Licensed under the Apache License, Version 2.0 (see LICENSE).

from dataclasses import dataclass
from typing import Tuple

from pants.core.goals.package import (
    BuiltPackage,
    BuiltPackageArtifact,
    OutputPathField,
    PackageFieldSet,
)
from pants.core.util_rules.archive import ArchiveFormat, CreateArchive
from pants.engine.addresses import AddressInput, UnparsedAddressInputs
from pants.engine.fs import AddPrefix, Digest, MergeDigests, RemovePrefix, Snapshot
from pants.engine.rules import Get, MultiGet, collect_rules, rule
from pants.engine.target import (
    COMMON_TARGET_FIELDS,
    Dependencies,
    FieldSetsPerTarget,
    FieldSetsPerTargetRequest,
    GeneratedSources,
    GenerateSourcesRequest,
    HydratedSources,
    HydrateSourcesRequest,
    Sources,
    StringField,
    StringSequenceField,
    Target,
    Targets,
    WrappedTarget,
)
from pants.engine.unions import UnionRule
from pants.option.global_options import GlobalOptions
from pants.util.logging import LogLevel

# -----------------------------------------------------------------------------------------------
# `files` target
# -----------------------------------------------------------------------------------------------


class FilesSources(Sources):
    required = True


class Files(Target):
    """A collection of loose files which do not have their source roots stripped.

    The sources of a `files` target can be accessed via language-specific APIs, such as Python's
    `open()`. Unlike the similar `resources()` target type, Pants will not strip the source root of
    `files()`, meaning that `src/python/project/f1.txt` will not be stripped down to
    `project/f1.txt`.
    """

    alias = "files"
    core_fields = (*COMMON_TARGET_FIELDS, Dependencies, FilesSources)


# -----------------------------------------------------------------------------------------------
# `relocated_files` target
# -----------------------------------------------------------------------------------------------


class RelocatedFilesSources(Sources):
    # We solely register this field for codegen to work.
    alias = "_sources"
    expected_num_files = 0


# TODO(#10888): Teach project introspection goals that this is a special type of the `Dependencies`
#  field.
class RelocatedFilesOriginalTargets(StringSequenceField):
    """Addresses to the original `files()` targets that you want to relocate, such as
    `['//:json_files']`.

    Every target will be relocated using the same mapping. This means that every target must include
    the value from the `src` field in their original path.
    """

    alias = "files_targets"
    required = True
    value: Tuple[str, ...]


class RelocatedFilesSrcField(StringField):
    """The original prefix that you want to replace, such as `src/resources`.

    You can set this field to `""` to preserve the original path; the value in the `dest` field will
    then be added to the beginning of this original path.
    """

    alias = "src"
    required = True


class RelocatedFilesDestField(StringField):
    """The new prefix that you want to add to the beginning of the path, such as `data`.

    You can set this field to `""` to avoid adding any new values to the path; the value in the
    `src` field will then be stripped, rather than replaced.
    """

    alias = "dest"
    required = True


class RelocatedFiles(Target):
    """Relocate the paths for `files()` targets at runtime to something more convenient than the
    default of their actual paths in your project.

    For example, you can relocate `src/resources/project1/data.json` to instead be
    `resources/data.json`. Your other target types can then add this target to their
    `dependencies` field, rather than using the original `files` target.

    To remove a prefix:

        # Results in `data.json`.
        relocated_files(
            file_targets=["src/resources/project1:target"],
            src="src/resources/project1",
            dest="",
        )

    To add a prefix:

        # Results in `images/logo.svg`.
        relocated_files(
            file_targets=["//:logo"],
            src="",
            dest="images",
        )

    To replace a prefix:

        # Results in `new_prefix/project1/data.json`.
        relocated_files(
            file_targets=["src/resources/project1:target"],
            src="src/resources",
            dest="new_prefix",
        )
    """

    alias = "relocated_files"
    core_fields = (
        *COMMON_TARGET_FIELDS,
        RelocatedFilesSources,
        RelocatedFilesOriginalTargets,
        RelocatedFilesSrcField,
        RelocatedFilesDestField,
    )


class RelocateFilesViaCodegenRequest(GenerateSourcesRequest):
    input = RelocatedFilesSources
    output = FilesSources


@rule(desc="Relocating loose files for `relocated_files` targets", level=LogLevel.DEBUG)
async def relocate_files(request: RelocateFilesViaCodegenRequest) -> GeneratedSources:
    # Unlike normal codegen, we operate the on the sources of the `files_targets` field, not the
    # `sources` of the original `relocated_sources` target.
    # TODO(#10915): using `await Get(Addresses, UnparsedAddressInputs)` causes a graph failure.
    original_files_targets = await MultiGet(
        Get(
            WrappedTarget,
            AddressInput,
            AddressInput.parse(v, relative_to=request.protocol_target.address.spec_path),
        )
        for v in request.protocol_target.get(RelocatedFilesOriginalTargets).value
    )
    original_files_sources = await MultiGet(
        Get(HydratedSources, HydrateSourcesRequest(wrapped_tgt.target.get(Sources)))
        for wrapped_tgt in original_files_targets
    )
    snapshot = await Get(
        Snapshot, MergeDigests(sources.snapshot.digest for sources in original_files_sources)
    )

    src_val = request.protocol_target.get(RelocatedFilesSrcField).value
    dest_val = request.protocol_target.get(RelocatedFilesDestField).value
    if src_val:
        snapshot = await Get(Snapshot, RemovePrefix(snapshot.digest, src_val))
    if dest_val:
        snapshot = await Get(Snapshot, AddPrefix(snapshot.digest, dest_val))
    return GeneratedSources(snapshot)


# -----------------------------------------------------------------------------------------------
# `resources` target
# -----------------------------------------------------------------------------------------------


class ResourcesSources(Sources):
    required = True


class Resources(Target):
    """A collection of loose files.

    The sources of a `resources` target can be accessed via language-specific APIs, such as Python's
    `open()`. Resources are meant to be included in deployable units like JARs or Python wheels.
    Unlike the similar `files()` target type, Pants will strip the source root of `resources()`,
    meaning that `src/python/project/f1.txt` will be stripped down to `project/f1.txt`.
    """

    alias = "resources"
    core_fields = (*COMMON_TARGET_FIELDS, Dependencies, ResourcesSources)


# -----------------------------------------------------------------------------------------------
# `target` generic target
# -----------------------------------------------------------------------------------------------


class GenericTarget(Target):
    """A generic target with no specific target type.

    This can be used as a generic "bag of dependencies", i.e. you can group several different
    targets into one single target so that your other targets only need to depend on one thing.
    """

    alias = "target"
    core_fields = (*COMMON_TARGET_FIELDS, Dependencies)


# -----------------------------------------------------------------------------------------------
# `archive` target
# -----------------------------------------------------------------------------------------------

# TODO(#10888): Teach project introspection goals that this is a special type of the `Dependencies`
#  field.
class ArchivePackages(StringSequenceField):
    """Addresses to any targets that can be built with `./pants package`.

    Pants will build the assets as if you had run `./pants package`. It will include the
    results in your archive using the same name they would normally have, but without the
    `--distdir` prefix (e.g. `dist/`).

    You can include anything that can be built by `./pants package`, e.g. a `python_binary`,
    `python_awslambda`, or even another `archive`.
    """

    alias = "packages"


# TODO(#10888): Teach project introspection goals that this is a special type of the `Dependencies`
#  field.
class ArchiveFiles(StringSequenceField):
    """Addresses to any `files` or `relocated_files` targets to include in the archive, e.g.
    `["resources:logo"]`.

    This is useful to include any loose files, like data files, image assets, or config files.

    This will ignore any targets that are not `files` or `relocated_files` targets. If you instead
    want those files included in any packages specified in the `packages` field for this target,
    then use a `resources` target and have the original package (e.g. the `python_library`)
    depend on the resources.
    """

    alias = "files"


class ArchiveFormatField(StringField):
    """The type of archive file to be generated."""

    alias = "format"
    valid_choices = ArchiveFormat
    required = True
    value: str


class ArchiveTarget(Target):
    """An archive (e.g. zip file) containing loose files and/or packages built via `./pants
    package`."""

    alias = "archive"
    core_fields = (
        *COMMON_TARGET_FIELDS,
        OutputPathField,
        ArchivePackages,
        ArchiveFiles,
        ArchiveFormatField,
    )


@dataclass(frozen=True)
class ArchiveFieldSet(PackageFieldSet):
    required_fields = (ArchiveFormatField,)

    packages: ArchivePackages
    files: ArchiveFiles
    format_field: ArchiveFormatField
    output_path: OutputPathField


@rule(level=LogLevel.DEBUG)
async def package_archive_target(
    field_set: ArchiveFieldSet, global_options: GlobalOptions
) -> BuiltPackage:
    package_targets, files_targets = await MultiGet(
        Get(
            Targets,
            UnparsedAddressInputs(field_set.packages.value or (), owning_address=field_set.address),
        ),
        Get(
            Targets,
            UnparsedAddressInputs(field_set.files.value or (), owning_address=field_set.address),
        ),
    )

    package_field_sets_per_target = await Get(
        FieldSetsPerTarget, FieldSetsPerTargetRequest(PackageFieldSet, package_targets)
    )
    packages = await MultiGet(
        Get(BuiltPackage, PackageFieldSet, field_set)
        for field_set in package_field_sets_per_target.field_sets
    )

    files_sources = await MultiGet(
        Get(
            HydratedSources,
            HydrateSourcesRequest(
                tgt.get(Sources), for_sources_types=(FilesSources,), enable_codegen=True
            ),
        )
        for tgt in files_targets
    )

    input_snapshot = await Get(
        Snapshot,
        MergeDigests(
            (
                *(package.digest for package in packages),
                *(sources.snapshot.digest for sources in files_sources),
            )
        ),
    )

    output_filename = field_set.output_path.value_or_default(
        field_set.address,
        file_ending=field_set.format_field.value,
        use_legacy_format=global_options.options.pants_distdir_legacy_paths,
    )
    archive = await Get(
        Digest,
        CreateArchive(
            input_snapshot,
            output_filename=output_filename,
            format=ArchiveFormat(field_set.format_field.value),
        ),
    )
    return BuiltPackage(archive, (BuiltPackageArtifact(output_filename),))


def rules():
    return (
        *collect_rules(),
        UnionRule(GenerateSourcesRequest, RelocateFilesViaCodegenRequest),
        UnionRule(PackageFieldSet, ArchiveFieldSet),
    )
