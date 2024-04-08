import re
import shutil
import string
from pathlib import Path
from typing import *  # type: ignore

import introspection
import revel
from revel import error, fatal, input, print, success, warning
from typing_extensions import TypeAlias

import rio.cli
import rio.snippets

__all__ = [
    "create_project",
]


def class_name_from_snippet(snip: rio.snippets.Snippet) -> str:
    """
    Given a file name, determine the name of the class that is defined in it.

    e.g. `sample_component.py` -> `SampleComponent`
    """
    assert snip.name.endswith(".py"), snip.name

    parts = snip.name[:-3].split("_")
    return "".join(part.capitalize() for part in parts)


def write_init_file(fil: IO, snippets: Iterable[rio.snippets.Snippet]) -> None:
    """
    Write an `__init__.py` file that imports all of the snippets.

    e.g. if told to import snippets `foo.py` and `bar.py`, it will write:

    ```
    from .foo import Foo
    from .bar import Bar
    ```
    """
    for snippet in snippets:
        assert snippet.name.endswith(".py"), snippet.name
        module_name = snippet.name[:-3]
        class_name = class_name_from_snippet(snippet)
        fil.write(f"from .{module_name} import {class_name}\n")


def generate_root_init(
    fil: TextIO,
    *,
    raw_name: str,
    project_type: Literal["app", "website"],
    components: List[rio.snippets.Snippet],
    pages: List[rio.snippets.Snippet],
    main_page_snippet: rio.snippets.Snippet,
    root_init_snippet: rio.snippets.Snippet,
    on_app_start: str | None = None,
) -> None:
    """
    Generate the `__init__.py` file for the main module of the project.
    """
    assert len(pages) > 0, pages

    # Prepare the different pages
    page_strings = []
    for snip in pages:
        # What's the URL segment for this page?
        if snip is main_page_snippet:
            url_segment = ""
        else:
            assert snip.name.endswith(".py"), snip.name
            url_segment = snip.name[:-3].replace("_", "-").lower()

        page_strings.append(
            f"""
        rio.Page(
            page_url={url_segment!r},
            build=pages.{class_name_from_snippet(snip)},
        ),"""
        )

    page_string = "\n".join(page_strings)

    # Imports
    default_theme = rio.Theme.from_color()
    fil.write(
        f"""
from __future__ import annotations

from pathlib import Path
from typing import *  # type: ignore

import rio

from . import pages
from . import components as comps
    """.strip()
    )

    # Additional imports
    try:
        additional_imports = root_init_snippet.get_section("additional-imports")
    except KeyError:
        pass
    else:
        fil.write("\n")
        fil.write(additional_imports)
        fil.write("\n\n")

    # Additional code
    try:
        additional_code = root_init_snippet.get_section("additional-code")
    except KeyError:
        pass
    else:
        fil.write("\n")
        fil.write(additional_code)
        fil.write("\n\n")

    # Theme & App generation
    fil.write(
        f"""
# Define a theme for Rio to use.
#
# You can modify the colors here to adapt the appearance of your app or website.
# The most important parameters are listed, but more are available! You can find
# them all in the docs TODO: Add link.
theme = rio.Theme.from_color(
    primary_color=rio.Color.from_hex("{default_theme.primary_color.hex}"),
    secondary_color=rio.Color.from_hex("{default_theme.secondary_color.hex}"),
    light=True,
)


# Create the Rio app
app = rio.App(
    name={raw_name!r},
    pages=[{page_string}
    ],
""".lstrip()
    )

    # Some parameters are optional
    if on_app_start is not None:
        fil.write(
            f"    # This function will be called once the app is ready.\n"
            f"    #\n"
            f"    # `rio run` will also call it again each time the app is reloaded.\n"
            f"    on_app_start={on_app_start},\n"
        )

    fil.write("    theme=theme,\n")
    fil.write("    assets_dir=Path(__file__).parent / \"assets\",\n")
    fil.write(")\n\n")


def strip_invalid_filename_characters(name: str) -> str:
    """
    Given a name, strip any characters that are not allowed in a filename.
    """
    return re.sub(r'[<>:"/\\|?*]', "", name)


def derive_module_name(raw_name: str) -> str:
    """
    Given an arbitrary string, derive similar and valid all_lower Python module
    identifier from it.
    """
    # Convert to lower_case
    name = introspection.convert_case(raw_name, "snake")

    # Strip any invalid characters
    name = "".join(c for c in name if c.isidentifier() or c in string.digits)

    # Since modules are written to files, the name also has to be a valid file
    # name
    name = strip_invalid_filename_characters(name)

    # Identifiers cannot start with a digit
    while name and name[0].isdigit():
        name = name[1:]

    # This could've resulted in an empty string
    if not name:
        name = "rio_app"

    # Done
    return name


def generate_readme(
    out: TextIO,
    raw_name: str,
    template: rio.snippets.ProjectTemplate,
) -> None:
    out.write(
        f"""# {raw_name}

This is a placeholder README for your project. Use it to describe what your
project is about, to give new users a quick overview of what they can expect.

_{raw_name.capitalize()}_ was created using [Rio](http://rio.dev/), an easy to
use app & website framework for Python._
"""
    )

    # Include the template's README
    if template.name != "Empty":
        out.write(
            f"""
This project is based on the `{template.name}` template.

## {template.name}

{template.description_markdown_source}
"""
        )


def write_component_file(
    out: TextIO,
    snip: rio.snippets.Snippet,
) -> None:
    """
    Writes the Python file containing a component or page to the given file.
    """
    # Common imports
    out.write(
        f"""from __future__ import annotations

from dataclasses import KW_ONLY, field
from typing import *  # type: ignore

import rio

from .. import components as comps

"""
    )

    # Additional, user-defined imports
    try:
        additional_imports = snip.get_section("additional-imports")
    except KeyError:
        pass
    else:
        out.write(additional_imports)
        out.write("\n")

    # The component proper
    out.write(snip.get_section("component"))


def generate_dependencies_file(project_dir: Path, dependencies: dict[str, str]) -> None:
    """
    Writes a `requirements.txt` file with the given dependencies. Does nothing
    if there are no dependencies.
    """
    # Anything to do?
    if not dependencies:
        return

    # requirements.txt
    with open(project_dir / "requirements.txt", "w") as out:
        for package, version_specifier in dependencies.items():
            out.write(f"{package}{version_specifier}\n")


def create_project(
    *,
    raw_name: str,
    type: Literal["app", "website"],
    template_name: rio.snippets.AvailableTemplatesLiteral,
) -> None:
    """
    Create a new project with the given name. This will directly interact with
    the terminal, asking for input and printing output.
    """

    # Derive a valid module name
    module_name = derive_module_name(raw_name)

    # The project directory is called the same, but in kebab-case
    dashed_name = module_name.replace("_", "-")

    # Find the template
    for template in rio.snippets.get_project_templates(include_empty=True):
        if template.name == template_name:
            break
    else:
        assert (
            False
        ), f"Received invalid template name `{template_name}`. This shouldn't be possible if the types are correct."

    # Create the target directory
    project_dir = Path.cwd() / dashed_name
    project_dir.mkdir(parents=True, exist_ok=True)

    # If the project directory already exists it must be empty
    if any(project_dir.iterdir()):
        fatal(f"The project directory `{project_dir}` already exists and is not empty")

    # Generate /rio.toml
    with open(project_dir / "rio.toml", "w") as f:
        f.write("# This is the configuration file for Rio,\n")
        f.write("# an easy to use app & web framework for Python.\n")
        f.write("\n")
        f.write(f"[app]\n")
        f.write(f'app_type = "{type}"  # This is either "website" or "app"\n')
        f.write(f'main_module = "{module_name}"  # The name of your Python module\n')

    # Create the main module and its subdirectories
    main_module_dir = project_dir / module_name
    assets_dir = main_module_dir / "assets"
    components_dir = main_module_dir / "components"
    pages_dir = main_module_dir / "pages"

    main_module_dir.mkdir(parents=True, exist_ok=True)
    assets_dir.mkdir()
    components_dir.mkdir()
    pages_dir.mkdir()

    # Generate /assets/*
    for snip in template.asset_snippets:
        source_path = snip.file_path
        target_path = assets_dir / source_path.name
        shutil.copyfile(source_path, target_path)

    # Generate /components/*.py
    for snip in template.component_snippets:
        target_path = components_dir / snip.name

        with target_path.open("w") as f:
            write_component_file(f, snip)

    # Generate pages/*.py
    for snip in template.page_snippets:
        target_path = pages_dir / snip.name

        with target_path.open("w") as f:
            write_component_file(f, snip)

    # Generate /*.py
    for snip in template.other_python_files:
        source_string = snip.stripped_code()
        target_path = main_module_dir / snip.name

        with target_path.open("w") as f:
            f.write(source_string)

    # Find the main page
    #
    # TODO: Right now this just uses the first and only page
    if len(template.page_snippets) > 1:
        raise NotImplementedError(f"TODO: Support more than one pages")

    main_page_snippet = template.page_snippets[0]

    # Generate /project/__init__.py
    with open(main_module_dir / "__init__.py", "w") as fil:
        generate_root_init(
            fil=fil,
            raw_name=raw_name,
            project_type=type,
            components=template.component_snippets,
            pages=template.page_snippets,
            main_page_snippet=main_page_snippet,
            root_init_snippet=template.root_init_snippet,
            on_app_start=template.on_app_start,
        )

    # Generate /project/components/__init__.py
    with open(main_module_dir / "components" / "__init__.py", "w") as f:
        write_init_file(f, template.component_snippets)

    # Generate /project/pages/__init__.py
    with open(main_module_dir / "pages" / "__init__.py", "w") as f:
        write_init_file(f, template.page_snippets)

    # Generate a file specifying all dependencies, if there are any
    generate_dependencies_file(project_dir, template.dependencies)

    # Generate README.md
    with open(project_dir / "README.md", "w") as f:
        generate_readme(f, raw_name, template)

    # Applications require a `__main__.py` as well
    if type == "app":
        with open(main_module_dir / "__main__.py", "w") as f:
            f.write(
                f"""
# Make sure the project is in the Python path
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))


# Import the main module
import {module_name}

# Run the app
{module_name}.app.run_in_window()
"""
            )

    # Report success
    #
    # TODO: Add a command to install dependencies? Activate the venv?
    print()
    success(f"The project has been created!")
    success(f"You can find it at `{project_dir.resolve()}`")
    print()
    print(f"To see your new project in action, run the following commands:")
    print()
    print(f"[dim]>[/] cd {revel.shell_escape(project_dir.resolve())}")

    if template.dependencies:
        print(
            f"[dim]>[/] python -m pip install -r requirements.txt  [bold]# Don't forget to install dependencies![/]"
        )  # TODO: Figure out the correct python command?

    print(f"[dim]>[/] rio run")