import auth


def test_principal_from_payload_preserves_roles_and_display_name():
    principal = auth.principal_from_payload(
        {
            "sub": "jane",
            "role": "admin",
            "roles": ["admin", "curator"],
            "display_name": "Jane Doe",
        }
    )

    assert principal.sub == "jane"
    assert principal.display_name == "Jane Doe"
    assert principal.has_role("admin")
    assert principal.has_role("curator")
    assert auth.principal_is_admin(principal)


def test_principal_from_payload_supports_string_roles():
    principal = auth.principal_from_payload({"sub": "maria", "role": "user", "roles": "curator, reviewer"})

    assert principal.roles == ("curator", "reviewer")
    assert not auth.principal_is_admin(principal)
    assert auth.principal_is_admin({"role": "admin"})
