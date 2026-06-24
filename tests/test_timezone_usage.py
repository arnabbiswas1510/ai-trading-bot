import os
import glob
import pytest

def test_no_date_today_usage():
    """
    Ensures that datetime.date.today() or date.today() is NOT used in the codebase,
    as this defaults to UTC on GitHub actions and causes timezone mismatch bugs for NYC dates.
    All dates should be calculated with NYC timezone:
    datetime.datetime.now(ZoneInfo("America/New_York")).date()
    """
    root_dir = os.path.dirname(os.path.dirname(__file__))
    python_files = glob.glob(os.path.join(root_dir, '**', '*.py'), recursive=True)
    
    banned_phrases = [
        "datetime.date.today()",
        "date.today()"
    ]
    
    offending_files = []
    
    for filepath in python_files:
        if "test_timezone_usage.py" in filepath or ".venv" in filepath or "site-packages" in filepath:
            continue
            
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
            
            for phrase in banned_phrases:
                # Basic check, if the banned phrase appears, flag it.
                # A more robust check could parse AST, but this is sufficient for preventing basic usage.
                if phrase in content:
                    offending_files.append((filepath, phrase))
                    
    assert not offending_files, f"Found banned timezone-naive date usage: {offending_files}"
