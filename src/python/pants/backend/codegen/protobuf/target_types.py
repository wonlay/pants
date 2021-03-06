# Copyright 2020 Pants project contributors (see CONTRIBUTORS.md).
# Licensed under the Apache License, Version 2.0 (see LICENSE).

from pants.backend.codegen.protobuf.protoc import Protoc
from pants.engine.addresses import Addresses, UnparsedAddressInputs
from pants.engine.rules import Get, collect_rules, rule
from pants.engine.target import (
    COMMON_TARGET_FIELDS,
    Dependencies,
    InjectDependenciesRequest,
    InjectedDependencies,
    Sources,
    Target,
)
from pants.engine.unions import UnionRule


class ProtobufDependencies(Dependencies):
    """Addresses to other targets that this target depends on, e.g. `['protobuf/example:lib']`.

    Pants will automatically inject any targets that you configure in the option `runtime_targets`
    in the `[protoc]` scope. For example, if you set that option to include the Python runtime
    library for Protobuf, every `protobuf_library` will automatically include that in its
    `dependencies`.
    """


class ProtobufSources(Sources):
    default = ("*.proto",)
    expected_file_extensions = (".proto",)


class ProtobufLibrary(Target):
    """Protobuf files used to generate various languages.

    See https://www.pantsbuild.org/docs/protobuf.
    """

    alias = "protobuf_library"
    core_fields = (*COMMON_TARGET_FIELDS, ProtobufDependencies, ProtobufSources)


class InjectProtobufDependencies(InjectDependenciesRequest):
    inject_for = ProtobufDependencies


@rule
async def inject_dependencies(
    _: InjectProtobufDependencies, protoc: Protoc
) -> InjectedDependencies:
    addresses = await Get(
        Addresses, UnparsedAddressInputs(protoc.runtime_targets, owning_address=None)
    )
    return InjectedDependencies(addresses)


def rules():
    return [
        *collect_rules(),
        UnionRule(InjectDependenciesRequest, InjectProtobufDependencies),
    ]
