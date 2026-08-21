"""HTML reporter package - re-exports generate_html_report and legacy helpers."""

from ici.reporters.html.report import generate_html_report
from ici.reporters.html.utils import (
    _cov_color,
    _escape_html_attr,
    _extract_suite_data,
    _get_status_theme,
    _location_controls,
    _status_color,
)

__all__ = [
    "_cov_color",
    "_escape_html_attr",
    "_extract_suite_data",
    "_get_status_theme",
    "_location_controls",
    "_status_color",
    "generate_html_report",
]
