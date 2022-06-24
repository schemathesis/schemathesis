import enum
import os
from typing import Dict, Optional

import attr
from typing_extensions import Protocol, runtime_checkable


@enum.unique
class CIProvider(enum.Enum):
    """A set of supported CI providers."""

    GITHUB = "github"
    GITLAB = "gitlab"


@runtime_checkable
class Environment(Protocol):
    provider: CIProvider

    def from_env(self) -> "Environment":
        pass

    def asdict(self) -> Dict[str, str]:
        pass


def environment() -> Optional[Environment]:
    """Collect environment data for a supported CI provider."""
    provider = detect()
    if provider == CIProvider.GITHUB:
        return GitHubActionsEnvironment.from_env()
    if provider == CIProvider.GITLAB:
        return GitLabCIEnvironment.from_env()
    return None


def detect() -> Optional[CIProvider]:
    """Detect the current CI provider."""
    if os.getenv("GITHUB_ACTIONS") == "true":
        return CIProvider.GITHUB
    if os.getenv("GITLAB_CI") == "true":
        return CIProvider.GITLAB
    return None


def asdict(env: Environment) -> Dict[str, str]:
    data = attr.asdict(env)
    data["provider"] = env.provider.value
    return data


@attr.s(slots=True)
class GitHubActionsEnvironment:
    """Useful data to capture from GitHub Actions environment."""

    provider = CIProvider.GITHUB
    asdict = asdict

    # GitHub API URL.
    # For example, `https://api.github.com`
    api_url: str = attr.ib()
    # The owner and repository name.
    # For example, `schemathesis/schemathesis`.
    repository: str = attr.ib()
    # The name of the person or app that initiated the workflow.
    # For example, `Stranger6667`
    actor: str = attr.ib()
    # The commit SHA that triggered the workflow.
    # For example, `e56e13224f08469841e106449f6467b769e2afca`
    sha: str = attr.ib()
    # The head ref or source branch of the pull request in a workflow run.
    # For example, `dd/report-ci`.
    head_ref: Optional[str] = attr.ib()
    # The name of the base ref or target branch of the pull request in a workflow run.
    # For example, `main`.
    base_ref: Optional[str] = attr.ib()
    # The branch or tag ref that triggered the workflow run.
    # This is only set if a branch or tag is available for the event type.
    # For example, `refs/pull/1533/merge`
    ref: Optional[str] = attr.ib()

    @classmethod
    def from_env(cls) -> "GitHubActionsEnvironment":
        return cls(
            api_url=os.environ["GITHUB_API_URL"],
            repository=os.environ["GITHUB_REPOSITORY"],
            actor=os.environ["GITHUB_ACTOR"],
            sha=os.environ["GITHUB_SHA"],
            head_ref=os.getenv("GITHUB_HEAD_REF"),
            base_ref=os.getenv("GITHUB_BASE_REF"),
            ref=os.getenv("GITHUB_REF"),
        )


@attr.s(slots=True)
class GitLabCIEnvironment:
    """Useful data to capture from GitLab CI environment."""

    provider = CIProvider.GITLAB
    asdict = asdict

    # GitLab API URL
    # For example, `https://gitlab.com/api/v4`
    api_v4_url: str = attr.ib()
    # The ID of the current project.
    # For example, `12345678`
    project_id: str = attr.ib()
    # The username of the user who started the job.
    # For example, `Stranger6667`
    user_login: str = attr.ib()
    # The commit revision the project is built for.
    # For example, `e56e13224f08469841e106449f6467b769e2afca`
    commit_sha: str = attr.ib()
    # NOTE: `commit_branch` and `merge_request_source_branch_name` may mean the same thing, but they are available
    # in different context. There are also a couple of `CI_BUILD_*` variables that could be used, but they are
    # not documented.
    # The commit branch name. Not available in merge request pipelines or tag pipelines.
    # For example, `dd/report-ci`.
    commit_branch: Optional[str] = attr.ib()
    # The source branch name of the merge request. Only available in merge request pipelines.
    # For example, `dd/report-ci`.
    merge_request_source_branch_name: Optional[str] = attr.ib()
    # The target branch name of the merge request.
    # For example, `main`.
    merge_request_target_branch_name: Optional[str] = attr.ib()
    # The project-level internal ID of the merge request.
    # For example, `42`.
    merge_request_iid: Optional[str] = attr.ib()

    @classmethod
    def from_env(cls) -> "GitLabCIEnvironment":
        return cls(
            api_v4_url=os.environ["CI_API_V4_URL"],
            project_id=os.environ["CI_PROJECT_ID"],
            user_login=os.environ["GITLAB_USER_LOGIN"],
            commit_sha=os.environ["CI_COMMIT_SHA"],
            commit_branch=os.getenv("CI_COMMIT_BRANCH"),
            merge_request_source_branch_name=os.getenv("CI_MERGE_REQUEST_SOURCE_BRANCH_NAME"),
            merge_request_target_branch_name=os.getenv("CI_MERGE_REQUEST_TARGET_BRANCH_NAME"),
            merge_request_iid=os.getenv("CI_MERGE_REQUEST_IID"),
        )
