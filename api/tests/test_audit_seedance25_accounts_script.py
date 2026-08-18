from __future__ import annotations

import base64
import importlib.util
import json
from pathlib import Path


def load_script():
    script = Path(__file__).parents[1] / "scripts" / "audit_seedance25_accounts.py"
    spec = importlib.util.spec_from_file_location("audit_seedance25_accounts", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def token(payload: dict[str, object]) -> str:
    encode = lambda value: base64.urlsafe_b64encode(  # noqa: E731
        json.dumps(value, separators=(",", ":")).encode()
    ).decode().rstrip("=")
    return f"{encode({'alg': 'none'})}.{encode(payload)}.signature"


def test_decodes_token_and_splits_export_line_with_pipe_in_password() -> None:
    module = load_script()

    assert module.decode_jwt_payload(token({"sub": "subject"}))["sub"] == "subject"
    assert module.split_export_line("user@example.test|pa|ss|jwt.value.sig") == (
        "user@example.test",
        "jwt.value.sig",
    )


def test_builds_launchdarkly_context_from_graphql_and_token_fields() -> None:
    module = load_script()

    context = module.build_launchdarkly_context(
        {
            "id": "user-id",
            "username": "fixture-user",
            "createdAt": "2026-08-16T00:00:00Z",
            "blocked": False,
        },
        {
            "auth0Email": "fixture@example.test",
            "plan": "BASIC",
            "showNsfw": False,
            "interests": ["video"],
            "interestsRoles": "CONSUMER",
            "interestsRolesOther": "",
        },
        [{"id": "team-id"}],
        {"custom:provider": "canva"},
        account_uuid="00000000-0000-0000-0000-000000000001",
        now=1_786_873_600,
    )

    assert context["kind"] == "multi"
    assert context["user"]["key"] == "user-id"
    assert context["user"]["emailDomain"] == "example.test"
    assert context["user"]["plan"] == "BASIC"
    assert context["user"]["hasActiveTeams"] is True
    assert context["user"]["teamsIds"] == ["team-id"]
    assert context["user"]["isCanvaForBusiness"] is True
    assert context["user"]["createdAt"] == 1_786_838_400_000
    assert context["user"]["accountAgeSeconds"] == 35_200


def test_parses_variable_precision_graphql_timestamp_as_utc() -> None:
    module = load_script()

    assert (
        module.timestamp_milliseconds("2026-08-15T15:20:28.37")
        == 1_786_807_228_370
    )


def test_parses_model_catalog_and_seedance_metadata() -> None:
    module = load_script()
    response = {
        "data": {
            "publicJsonSchemaRegistry": {
                "release": {
                    "id": "1.260.0",
                    "status": "ACTIVE",
                    "schemaReferences": [
                        {
                            "schemaId": "schema/seedance25",
                            "schemaData": {
                                "properties": {
                                    "model": {
                                        "const": "bytedance/seedance-2.5",
                                        "ui:metadata": {
                                            "flag": "isSeedance25Enabled",
                                            "order": 10,
                                            "is_featured": True,
                                        },
                                        "leo:model_config": {
                                            "id": "model-id",
                                            "name": "Seedance 2.5",
                                            "type": "video",
                                            "capabilities": {
                                                "generate": True,
                                                "production_api_availability": True,
                                            },
                                        },
                                    }
                                }
                            },
                        },
                        {
                            "schemaId": "schema/not-a-model",
                            "schemaData": {"properties": {"model": {"type": "string"}}},
                        },
                    ],
                }
            }
        }
    }

    catalog = module.parse_model_catalog(response)

    assert catalog["release_id"] == "1.260.0"
    assert catalog["model_count"] == 1
    assert catalog["models"] == ["bytedance/seedance-2.5"]
    assert catalog["seedance25"] == {
        "slug": "bytedance/seedance-2.5",
        "id": "model-id",
        "name": "Seedance 2.5",
        "type": "video",
        "ui_flag": "isSeedance25Enabled",
        "featured": True,
        "generate": True,
        "production_api_availability": True,
        "schema_id": "schema/seedance25",
        "order": 10,
    }


def test_applies_account_availability_from_flag_and_catalog() -> None:
    module = load_script()
    results = [
        {"model_release": "release", "feature_flag": True, "has_seedance25": None},
        {"model_release": "release", "feature_flag": False, "has_seedance25": None},
    ]
    catalogs = {
        "release": {
            "seedance25": {"slug": "bytedance/seedance-2.5", "generate": True}
        }
    }

    module.apply_catalog_verdicts(results, catalogs)

    assert results[0]["has_seedance25"] is True
    assert results[0]["reason"] == "enabled"
    assert results[1]["has_seedance25"] is False
    assert results[1]["reason"] == "feature_flag_disabled"


def test_launchdarkly_flag_supports_detailed_and_plain_values() -> None:
    module = load_script()

    assert module.launchdarkly_flag_value({"flag": {"value": True}}, "flag") is True
    assert module.launchdarkly_flag_value({"flag": "1.260.0"}, "flag") == "1.260.0"


def test_builds_direct_database_context_from_expired_token_claims() -> None:
    module = load_script()
    payload = {
        "exp": 1,
        "auth_time": 1_786_838_400,
        "email": "fixture@example.test",
        "cognito:username": "fixture-user",
        "custom:provider": "canva",
        "https://hasura.io/jwt/claims": json.dumps(
            {"x-hasura-user-id": "hasura-user-id"}
        ),
    }

    context = module.build_launchdarkly_context_from_token(
        payload,
        registration_uuid="00000000-0000-0000-0000-000000000001",
        plan="FREE",
        now=1_786_873_600,
    )

    assert context["user"]["key"] == "hasura-user-id"
    assert context["user"]["emailDomain"] == "example.test"
    assert context["user"]["createdAt"] == 1_786_838_400_000
    assert context["user"]["accountAgeSeconds"] == 35_200
    assert context["user"]["isCanvaForBusiness"] is True
    assert context["user"]["plan"] == "FREE"


def test_report_identity_is_redacted() -> None:
    module = load_script()

    result = module.redacted_registration(
        {
            "registration_uuid": "registration-id",
            "login_name": "private@example.test",
            "status": "SUCCEEDED",
            "is_used": False,
            "awarded_points": 8500,
            "token_expires_at": "2026-08-16T12:00:00",
        }
    )

    assert result["registration_uuid"] == "registration-id"
    assert result["email_domain"] == "example.test"
    assert result["email_sha256"]
    assert "private" not in json.dumps(result)


def test_parser_defaults_to_unused_8500_successful_registrations() -> None:
    module = load_script()

    args = module.build_parser().parse_args([])

    assert args.status == "SUCCEEDED"
    assert args.credits == 8500
    assert args.used is False
    assert args.offset == 0
    assert args.limit == 0
    assert args.plan == "BASIC"
