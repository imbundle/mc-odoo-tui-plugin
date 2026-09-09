from module_update_planning import ModulePlanningError, plan_module_updates


CATALOG = [
    {"name": "zebra", "display_name": "Zebra Tools", "installed": True, "update_available": True},
    {"name": "alpha", "translations": {"it_IT": "Strumenti Alpha"}, "installed": True, "update_available": True},
    {"name": "base", "display_name": "Base", "installed": True, "update_available": False},
    {"name": "uninstalled", "display_name": "Not Installed", "installed": False, "update_available": True},
]


def raises_code(code, fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
    except ModulePlanningError as error:
        assert error.code == code
    else:
        raise AssertionError(f"expected {code}")


def test_translated_and_canonical_identifiers_resolve_to_canonical_names():
    translated = plan_module_updates(CATALOG, selected="Strumenti Alpha")
    assert translated["module_names"] == ["alpha"]
    assert translated["modules"][0]["requested_as"] == "Strumenti Alpha"
    canonical = plan_module_updates(CATALOG, selected="zebra")
    assert canonical["module_names"] == ["zebra"]


def test_selected_plan_filters_non_updates_and_preserves_catalog_order():
    result = plan_module_updates(CATALOG, selected=["alpha", "zebra", "base"])
    assert result["module_names"] == ["zebra", "alpha"]
    assert result["skipped"] == ["base"]
    assert result["requires_approval"] is True


def test_update_all_includes_only_installed_updates_in_stable_order():
    result = plan_module_updates(CATALOG, update_all=True)
    assert result["kind"] == "update_all"
    assert result["module_names"] == ["zebra", "alpha"]
    assert result["skipped"] == ["base", "uninstalled"]


def test_missing_ambiguous_uninstalled_and_invalid_selection_are_structured():
    raises_code("MODULE_NOT_FOUND", plan_module_updates, CATALOG, selected="missing")
    ambiguous = [
        {"name": "one", "translated_name": "Same", "installed": True},
        {"name": "two", "translated_name": "Same", "installed": True},
    ]
    raises_code("AMBIGUOUS_MODULE_TRANSLATION", plan_module_updates, ambiguous, selected="Same")
    raises_code("MODULE_NOT_INSTALLED", plan_module_updates, CATALOG, selected="Not Installed")
    raises_code("INVALID_SELECTION", plan_module_updates, CATALOG)
    raises_code("INVALID_SELECTION", plan_module_updates, CATALOG, selected=[""])


def test_empty_update_sets_are_explicit():
    raises_code("EMPTY_UPDATE_SET", plan_module_updates, CATALOG, selected="base")
    raises_code("EMPTY_UPDATE_SET", plan_module_updates, [{"name": "base", "installed": True, "update_available": False}], update_all=True)


def test_invalid_catalog_and_selection_never_escape_as_type_errors():
    raises_code("INVALID_MODULE_METADATA", plan_module_updates, {"name": "sale"}, update_all=True)
    raises_code("INVALID_SELECTION", plan_module_updates, CATALOG, selected=object())
    raises_code(
        "INVALID_MODULE_METADATA",
        plan_module_updates,
        [{"name": "sale"}, {"name": "sale"}],
        update_all=True,
    )
