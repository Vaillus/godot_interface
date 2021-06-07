import os
import re

def get_path(string_path, add_absolute=False):
    """ fromats the path to a format that is correct to python. Can also 
    add its absolute path prefix.
    Returns:
        string: absolute path that is correct to python
    """
    modified_string_path = ""
    if add_absolute:
        modified_string_path = os.path.abspath(os.path.join(os.sep, *string_path.split("/")))
    else:
        modified_string_path = os.path.join(*string_path.split("/"))
    return modified_string_path

def get_username() -> str:
    # username = os.path.expanduser("~").split("/")[-1]
    users = os.listdir("/mnt/c/Users")
    if "vaill" in users:
        username = "vaill"
    elif "Hugo" in users:
        username = "Hugo"
    return username

def get_godot_path() -> str:
    """specific to my personal use.
    I made it so I can run Godot from python on any of my computers.

    Returns:
        str: godot path
    """
    start_path = "mnt/c/Users/"
    username = get_username()
    if username == "vaill":

        path = '"/mnt/c/Program Files (x86)/Steam/steamapps/common/Godot Engine/godot.windows.opt.tools.64.exe"'
        return path
    end_path = "Desktop"#/Godot_v3.2.3-stable_win64.exe"
    desktop_path = start_path.split("/") + [username] + end_path.split("/")
    godot_file = find_godot(desktop_path)
    list_path = desktop_path + [godot_file]
    godot_path = os.path.join(*list_path)
    return godot_path

def find_godot(path: list) -> str:
    desktop_files = os.listdir(os.sep+os.path.join(*path))
    godot_file = ""
    for file_name in desktop_files:
        if re.match(r"godot.*", file_name.lower()):
            godot_file = file_name
    assert godot_file != "", f"There is no godot file in {os.sep+os.path.join(*path)}"
    return godot_file


def get_godot_package_path(package_name: str) -> str:
    """specific to my personal use.
    I made it so I can run Godot packages from python on any of my computers.

    Args:
        package_name (str): name of the package, with or without extension

    Returns:
        str: [description]
    """
    package_name = add_extension(package_name, "pck")
    start_path = "C:/Users"
    username = get_username()
    mid_path = "Documents/work/projects/flight_simulator/"
    total_path_list = start_path.split("/") + [username] + mid_path.split("/") + [package_name]
    total_path = os.path.join(*total_path_list)
    return total_path

def add_extension(  file_name: str,
                    extension: str) -> str:
    """add extension to filename if not already there.
    """
    file_name, file_extension = os.path.splitext(file_name)
    if file_extension == "":
        extension = extension
    file_name += "." + extension
    return file_name
