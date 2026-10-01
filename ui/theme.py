"""Theme helpers and calm indicator stylesheets for Refer."""
from pathlib import Path

RESOURCES_DIR = Path(__file__).parent / "resources"


def get_indicator_stylesheet() -> str:
    """Returns CSS rules for calm checkboxes in QTreeWidget and QCheckBox."""
    checked = (RESOURCES_DIR / "checkbox_checked.svg").resolve().as_posix()
    checked_hover = (RESOURCES_DIR / "checkbox_checked_hover.svg").resolve().as_posix()
    indeterminate = (RESOURCES_DIR / "checkbox_indeterminate.svg").resolve().as_posix()
    indeterminate_hover = (RESOURCES_DIR / "checkbox_indeterminate_hover.svg").resolve().as_posix()
    unchecked = (RESOURCES_DIR / "checkbox_unchecked.svg").resolve().as_posix()
    unchecked_hover = (RESOURCES_DIR / "checkbox_unchecked_hover.svg").resolve().as_posix()

    return f"""
        QTreeWidget::indicator, QCheckBox::indicator {{
            width: 14px;
            height: 14px;
            border: none;
            background: transparent;
        }}
        QTreeWidget::indicator:unchecked, QCheckBox::indicator:unchecked {{
            image: url("{unchecked}");
        }}
        QTreeWidget::indicator:unchecked:hover, QCheckBox::indicator:unchecked:hover {{
            image: url("{unchecked_hover}");
        }}
        QTreeWidget::indicator:checked, QCheckBox::indicator:checked {{
            image: url("{checked}");
        }}
        QTreeWidget::indicator:checked:hover, QCheckBox::indicator:checked:hover {{
            image: url("{checked_hover}");
        }}
        QTreeWidget::indicator:indeterminate, QCheckBox::indicator:indeterminate {{
            image: url("{indeterminate}");
        }}
        QTreeWidget::indicator:indeterminate:hover, QCheckBox::indicator:indeterminate:hover {{
            image: url("{indeterminate_hover}");
        }}
        QTreeWidget::indicator:disabled, QCheckBox::indicator:disabled {{
            opacity: 0.4;
        }}
    """
