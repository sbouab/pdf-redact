"""The names of the files the tool writes next to an original, shared by the command line and the app."""
import os

REVIEW = " - REVIEW (not redacted)"
REDACTED = " - REDACTED"
FAILED = " - FAILED CHECK (do not share)"


def split_name(path):
    """(folder, base name, the tool's suffix or None) of a PDF path."""
    folder, name = os.path.split(os.path.abspath(path))
    stem = name[:-4] if name.lower().endswith(".pdf") else name
    for suffix in (REVIEW, REDACTED, FAILED):
        if stem.lower().endswith(suffix.lower()):
            return folder, stem[:-len(suffix)], suffix
    return folder, stem, None


def output_path(original, suffix):
    """Where the tool writes the file of kind `suffix` made from `original` (or from one of its outputs)."""
    folder, stem, _ = split_name(original)
    return os.path.join(folder, stem + suffix + ".pdf")
