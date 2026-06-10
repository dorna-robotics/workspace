# Project-local components live here. Drop a module with a
# ``@register("your_type")`` class in this folder and the canonical
# ``main.py`` auto-imports the whole ``components/`` package on boot
# (mirrors how the library's components package auto-registers), so the
# scene yaml's ``type:`` lookup resolves to your class. No manual import
# needed.
#
# See docs/component-guide.md §3 "Where to put it" for the full pattern.
