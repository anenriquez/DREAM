"""
File for writing a dictionary to
"""

import os.path
import pandas


def save_csv_row(row, to_file):
    """ Write dictionary to a file in CSV format.

    Args:
        row (dict): Row to write. Keys are the columns.
        to_file (str): File path to write to.
    """
    df = pd.DataFrame.from_dict(row)
    if os.path.isfile(to_file):
        # File exists, there should also be a header line then.
        df.to_csv(to_file, index=False, header=False, mode='a',
                  encoding='utf-8')
    else:
        # File does not exist, make it.
        df.to_csv(to_file, index=False, header=True, mode='w',
                  encoding='utf-8')
