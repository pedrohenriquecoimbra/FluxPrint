try:
    from ffpkljun import *
except ModuleNotFoundError:
    print("ModuleNotFoundError: No module named 'ffpkljun'. Use 'pip install' or please contact moderator.")
    get_contour_levels = None
    get_contour_vertices = None