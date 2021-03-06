# Copyright 2015 Pants project contributors (see CONTRIBUTORS.md).
# Licensed under the Apache License, Version 2.0 (see LICENSE).

import dataclasses
import difflib
import json
import textwrap
from typing import Dict, cast

from typing_extensions import Literal

from pants.base.build_environment import pants_version
from pants.help.help_formatter import HelpFormatter
from pants.help.help_info_extracter import AllHelpInfo, HelpJSONEncoder
from pants.help.maybe_color import MaybeColor
from pants.option.arg_splitter import (
    AllHelp,
    GoalsHelp,
    HelpRequest,
    NoGoalHelp,
    OptionsHelp,
    UnknownGoalHelp,
    VersionHelp,
)
from pants.option.scope import GLOBAL_SCOPE


class HelpPrinter(MaybeColor):
    """Prints general and goal-related help to the console."""

    def __init__(
        self,
        *,
        bin_name: str,
        help_request: HelpRequest,
        all_help_info: AllHelpInfo,
        color: bool,
    ) -> None:
        super().__init__(color)
        self._bin_name = bin_name
        self._help_request = help_request
        self._all_help_info = all_help_info

    def print_help(self) -> Literal[0, 1]:
        """Print help to the console."""

        def print_hint() -> None:
            print(f"Use `{self.maybe_green(self._bin_name + ' goals')}` to list goals.")
            print(f"Use `{self.maybe_green(self._bin_name + ' help')}` to get help.")

        if isinstance(self._help_request, VersionHelp):
            print(pants_version())
        elif isinstance(self._help_request, GoalsHelp):
            self._print_goals_help()
        elif isinstance(self._help_request, AllHelp):
            self._print_all_help()
        elif isinstance(self._help_request, OptionsHelp):
            self._print_options_help()
        elif isinstance(self._help_request, UnknownGoalHelp):
            # Only print help and suggestions for the first unknown goal.
            # It gets confusing to try and show suggestions for multiple cases.
            unknown_goal = self._help_request.unknown_goals[0]
            print(f"Unknown goal: {self.maybe_red(unknown_goal)}")
            did_you_mean = list(
                difflib.get_close_matches(
                    unknown_goal, self._all_help_info.name_to_goal_info.keys()
                )
            )
            if did_you_mean:
                print(f"Did you mean: {', '.join(self.maybe_cyan(g) for g in did_you_mean)}?")
            print_hint()
            return 1
        elif isinstance(self._help_request, NoGoalHelp):
            print("No goals specified.")
            print_hint()
            return 1
        return 0

    def _print_goals_help(self) -> None:
        goal_descriptions: Dict[str, str] = {}

        for goal_info in self._all_help_info.name_to_goal_info.values():
            if goal_info.is_implemented:
                goal_descriptions[goal_info.name] = goal_info.description

        title_text = "Goals"
        title = self.maybe_green(f"{title_text}\n{'-' * len(title_text)}")

        max_width = max((len(name) for name in goal_descriptions.keys()), default=0)
        chars_before_description = max_width + 2

        def format_goal(name: str, descr: str) -> str:
            name = self.maybe_cyan(name.ljust(chars_before_description))
            description_lines = textwrap.wrap(descr, 80 - chars_before_description)
            if len(description_lines) > 1:
                description_lines = [
                    description_lines[0],
                    *(f"{' ' * chars_before_description}{line}" for line in description_lines[1:]),
                ]
            formatted_descr = "\n".join(description_lines)
            return f"{name}{formatted_descr}\n"

        lines = [
            f"\n{title}\n",
            f"Use `{self._bin_name} help $goal` to get help for a particular goal.",
            "\n",
            *(
                format_goal(name, description)
                for name, description in sorted(goal_descriptions.items())
            ),
        ]
        print("\n".join(lines))

    def _print_all_help(self) -> None:
        print(self._get_help_json())

    def _print_options_help(self) -> None:
        """Print a help screen.

        Assumes that self._help_request is an instance of OptionsHelp.

        Note: Ony useful if called after options have been registered.
        """
        help_request = cast(OptionsHelp, self._help_request)
        # The scopes explicitly mentioned by the user on the cmd line.
        help_scopes = set(help_request.scopes)
        if help_scopes:
            for scope in sorted(help_scopes):
                help_str = self._format_help(scope, help_request.advanced)
                if help_str:
                    print(help_str)
            return
        else:
            self._print_global_help(help_request.advanced)

    def _print_global_help(self, advanced: bool):
        print(f"Pants {pants_version()} https://pypi.org/pypi/pantsbuild.pants/{pants_version()}")
        print("\nUsage:")
        print(
            f"  {self._bin_name} [option ...] [goal ...] [target/file ...]  Attempt the specified goals."
        )
        print(f"  {self._bin_name} help                                       Get global help.")
        print(
            f"  {self._bin_name} help [goal/subsystem]                      Get help for a goal or subsystem."
        )
        print(
            f"  {self._bin_name} help-advanced                              Get help for global advanced options."
        )
        print(
            f"  {self._bin_name} help-advanced [goal/subsystem]             Get help for a goal's or subsystem's advanced options."
        )
        print(
            f"  {self._bin_name} help-all                                   Get a JSON object containing all help info."
        )
        print(
            f"  {self._bin_name} goals                                      List all installed goals."
        )
        print("")
        print("  [file] can be:")
        print("     A path to a file.")
        print("     A path glob, such as '**/*.ext', in quotes to prevent shell expansion.")
        print("  [target] accepts two special forms:")
        print("    dir:  to include all targets in the specified directory.")
        print("    dir:: to include all targets found recursively under the directory.\n")
        print("More documentation is available at https://www.pantsbuild.org")

        print(self._format_help(GLOBAL_SCOPE, advanced))

    def _format_help(self, scope: str, show_advanced_and_deprecated: bool) -> str:
        """Return a human-readable help message for the options registered on this object.

        Assumes that self._help_request is an instance of OptionsHelp.
        """
        help_formatter = HelpFormatter(
            show_advanced=show_advanced_and_deprecated,
            show_deprecated=show_advanced_and_deprecated,
            color=self.color,
        )
        oshi = self._all_help_info.scope_to_help_info.get(scope)
        if not oshi:
            return ""
        formatted_lines = help_formatter.format_options(oshi)
        goal_info = self._all_help_info.name_to_goal_info.get(scope)
        if goal_info:
            related_scopes = sorted(set(goal_info.consumed_scopes) - {GLOBAL_SCOPE, goal_info.name})
            if related_scopes:
                related_subsystems_label = self.maybe_green("Related subsystems:")
                formatted_lines.append(f"{related_subsystems_label} {', '.join(related_scopes)}")
                formatted_lines.append("")
        return "\n".join(formatted_lines).rstrip()

    def _get_help_json(self) -> str:
        """Return a JSON object containing all the help info we have, for every scope."""
        return json.dumps(
            dataclasses.asdict(self._all_help_info), sort_keys=True, indent=2, cls=HelpJSONEncoder
        )
