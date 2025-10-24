# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import os
import sys


def get_absolute_path(filename: str):
    """
    Gets the absolute path to the specified file in the folder where the script is currently executed.

    :param filename: The name of the file to be found in the same directory as the script
    :return: Specifies the absolute path string of the file
    """
    if hasattr(sys, '_MEIPASS') and getattr(sys, 'frozen', False):
        base_path = sys._MEIPASS
    else:
        base_path = os.path.dirname(os.path.abspath(__file__))

    return os.path.join(base_path, filename)
