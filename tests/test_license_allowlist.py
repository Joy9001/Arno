from scripts import license_audit


def test_license_audit_rejects_forbidden_and_unrecognized_licenses():
    packages = {
        name: {"version": "1", "license": license_name}
        for name, license_name in {
            "allowed": "MIT",
            "forbidden": "GPL-3.0",
            "unlisted": "BUSL-1.1",
            "unknown": "UNKNOWN",
        }.items()
    }
    assert [failure.split()[0] for failure in license_audit.failures(packages)] == [
        "forbidden",
        "unknown",
        "unlisted",
    ]
    assert "UNKNOWN" in license_audit._expressions("MIT OR BUSL-1.1")
