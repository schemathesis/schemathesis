import pytest

from schemathesis.service import ci


@pytest.mark.parametrize(
    "env, expected",
    (
        (
            {
                "GITHUB_ACTIONS": "true",
                "GITHUB_API_URL": "https://api.github.com",
                "GITHUB_REPOSITORY": "schemathesis/schemathesis",
                "GITHUB_ACTOR": "Stranger6667",
                "GITHUB_SHA": "e56e13224f08469841e106449f6467b769e2afca",
                "GITHUB_RUN_ID": "1658821493",
                "GITHUB_WORKFLOW": "Build job",
                "GITHUB_HEAD_REF": "dd/report-ci",
                "GITHUB_BASE_REF": "main",
                "GITHUB_REF": "refs/pull/1533/merge",
            },
            ci.GitHubActionsEnvironment(
                api_url="https://api.github.com",
                repository="schemathesis/schemathesis",
                actor="Stranger6667",
                sha="e56e13224f08469841e106449f6467b769e2afca",
                run_id="1658821493",
                workflow="Build job",
                head_ref="dd/report-ci",
                base_ref="main",
                ref="refs/pull/1533/merge",
                action_ref=None,
            ),
        ),
        (
            {
                "GITLAB_CI": "true",
                "CI_API_V4_URL": "https://gitlab.com/api/v4",
                "CI_PROJECT_ID": "7",
                "GITLAB_USER_LOGIN": "Stranger6667",
                "CI_COMMIT_SHA": "e56e13224f08469841e106449f6467b769e2afca",
                "CI_MERGE_REQUEST_SOURCE_BRANCH_NAME": "dd/report-ci",
                "CI_MERGE_REQUEST_TARGET_BRANCH_NAME": "main",
                "CI_MERGE_REQUEST_IID": "43",
            },
            ci.GitLabCIEnvironment(
                api_v4_url="https://gitlab.com/api/v4",
                project_id="7",
                user_login="Stranger6667",
                commit_sha="e56e13224f08469841e106449f6467b769e2afca",
                commit_branch=None,
                merge_request_source_branch_name="dd/report-ci",
                merge_request_target_branch_name="main",
                merge_request_iid="43",
            ),
        ),
        (
            {},
            None,
        ),
    ),
    ids=["github", "gitlab", "none"],
)
def test_environment(monkeypatch, env, expected):
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    environment = ci.environment()
    assert environment == expected
    if environment is not None:
        assert environment.asdict()["provider"] == environment.provider.value
