import os

def get_path(string_path, add_absolute=False):
    """ fromats the path to a format that is correct to python. Can also add its absolute path prefix.

    Returns:
        string: absolute path that is correct to python
    """
    modified_string_path = ""
    if add_absolute:
        modified_string_path = os.path.abspath(os.path.join(os.sep, *string_path.split("/")))
    else:
        modified_string_path = os.path.join(*string_path.split("/"))
    return modified_string_path