# -*- encoding: utf-8 -*-
#
# Copyright © 2018–2020 Mergify SAS
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.
import dataclasses
import typing
from unittest import mock

from freezegun import freeze_time
import pytest
import voluptuous

from mergify_engine import check_api
from mergify_engine import context
from mergify_engine import date
from mergify_engine import rules
from mergify_engine.actions import merge_base
from mergify_engine.rules import conditions


@dataclasses.dataclass
class FakeQueuePullRequest:
    attrs: typing.Dict[str, context.ContextAttributeType]

    async def __getattr__(self, name: str) -> context.ContextAttributeType:
        fancy_name = name.replace("_", "-")
        return self.attrs[fancy_name]

    def sync_checks(self) -> None:
        self.attrs["check-success-or-neutral-or-pending"] = (
            self.attrs.get("check-success", [])  # type: ignore
            + self.attrs.get("check-neutral", [])  # type: ignore
            + self.attrs.get("check-pending", [])  # type: ignore
        )
        self.attrs["check"] = (
            self.attrs.get("check-success", [])  # type: ignore
            + self.attrs.get("check-neutral", [])  # type: ignore
            + self.attrs.get("check-pending", [])  # type: ignore
            + self.attrs.get("check-failure", [])  # type: ignore
        )


@pytest.mark.asyncio
async def test_rules_conditions_update():
    pulls = [
        FakeQueuePullRequest(
            {
                "number": 1,
                "current-year": date.Year(2018),
                "author": "me",
                "base": "main",
                "head": "feature-1",
                "label": ["foo", "bar"],
                "check-success": ["tests"],
                "check-pending": [],
                "check-failure": ["jenkins/fake-tests"],
            }
        ),
    ]
    pulls[0].sync_checks()
    schema = voluptuous.Schema(
        voluptuous.All(
            [voluptuous.Coerce(rules.RuleConditionSchema)],
            voluptuous.Coerce(conditions.QueueRuleConditions),
        )
    )

    c = schema(
        [
            "label=foo",
            "check-success=tests",
            "check-success=jenkins/fake-tests",
        ]
    )

    await c(pulls)

    assert (
        c.get_summary()
        == """- `label=foo`
  - [X] #1
- [X] `check-success=tests`
- [ ] `check-success=jenkins/fake-tests`
"""
    )

    state = await merge_base.get_rule_checks_status(
        mock.Mock(), pulls, mock.Mock(conditions=c)
    )
    assert state == check_api.Conclusion.FAILURE


async def assert_queue_rule_checks_status(conds, pull, expected_state):
    schema = voluptuous.Schema(
        voluptuous.All(
            [voluptuous.Coerce(rules.RuleConditionSchema)],
            voluptuous.Coerce(conditions.QueueRuleConditions),
        )
    )

    c = schema(conds)

    await c([pull])
    state = await merge_base.get_rule_checks_status(
        mock.Mock(),
        [pull],
        mock.Mock(conditions=c),
        unmatched_conditions_return_failure=False,
    )
    assert state == expected_state


@pytest.mark.xfail(True, reason="not yet supported", strict=True)
@pytest.mark.asyncio
async def test_rules_checks_status_with_negative_conditions():
    pull = FakeQueuePullRequest(
        {
            "number": 1,
            "current-year": date.Year(2018),
            "author": "me",
            "base": "main",
            "head": "feature-1",
            "check-success": [],
            "check-failure": [],
            "check-pending": [],
            "check": [],
            "check-success-or-neutral-or-pending": [],
        }
    )
    conds = [
        "check-success=test-starter",
        "-check-pending=foo",
        "-check-failure=foo",
    ]

    # Nothing reported
    await assert_queue_rule_checks_status(conds, pull, check_api.Conclusion.PENDING)

    # Pending reported
    pull.attrs["check-pending"] = ["foo"]
    pull.attrs["check-failure"] = []
    pull.attrs["check-success"] = ["test-starter"]
    pull.sync_checks()
    await assert_queue_rule_checks_status(conds, pull, check_api.Conclusion.PENDING)

    # Failure reported
    pull.attrs["check-pending"] = []
    pull.attrs["check-failure"] = ["foo"]
    pull.attrs["check-success"] = ["test-starter"]
    pull.sync_checks()
    await assert_queue_rule_checks_status(conds, pull, check_api.Conclusion.FAILURE)

    # Success reported
    pull.attrs["check-pending"] = []
    pull.attrs["check-failure"] = []
    pull.attrs["check-success"] = ["test-starter", "foo"]
    pull.sync_checks()
    await assert_queue_rule_checks_status(conds, pull, check_api.Conclusion.SUCCESS)

    # half reported, sorry..., UNDEFINED BEHAVIOR
    pull.attrs["check-pending"] = []
    pull.attrs["check-failure"] = []
    pull.attrs["check-success"] = ["test-starter"]
    pull.sync_checks()
    await assert_queue_rule_checks_status(conds, pull, check_api.Conclusion.SUCCESS)


@pytest.mark.asyncio
async def test_rules_checks_status_with_or_conditions():
    pull = FakeQueuePullRequest(
        {
            "number": 1,
            "current-year": date.Year(2018),
            "author": "me",
            "base": "main",
            "head": "feature-1",
            "check-success": [],
            "check-failure": [],
            "check-pending": [],
            "check": [],
            "check-success-or-neutral-or-pending": [],
        }
    )
    conds = [
        {
            "or": ["check-success=ci-1", "check-success=ci-2"],
        }
    ]

    # Nothing reported
    await assert_queue_rule_checks_status(conds, pull, check_api.Conclusion.PENDING)

    # Pending reported
    pull.attrs["check-pending"] = ["ci-1"]
    pull.attrs["check-failure"] = []
    pull.attrs["check-success"] = ["ci-2"]
    pull.sync_checks()
    await assert_queue_rule_checks_status(conds, pull, check_api.Conclusion.SUCCESS)

    # Pending reported
    pull.attrs["check-pending"] = ["ci-1"]
    pull.attrs["check-failure"] = ["ci-2"]
    pull.attrs["check-success"] = []
    pull.sync_checks()
    await assert_queue_rule_checks_status(conds, pull, check_api.Conclusion.PENDING)

    # Failure reported
    pull.attrs["check-pending"] = []
    pull.attrs["check-failure"] = ["ci-1"]
    pull.attrs["check-success"] = ["ci-2"]
    pull.sync_checks()
    await assert_queue_rule_checks_status(conds, pull, check_api.Conclusion.SUCCESS)

    # Success reported
    pull.attrs["check-pending"] = []
    pull.attrs["check-failure"] = []
    pull.attrs["check-success"] = ["ci-1", "ci-2"]
    pull.sync_checks()
    await assert_queue_rule_checks_status(conds, pull, check_api.Conclusion.SUCCESS)

    # half reported success
    pull.attrs["check-failure"] = []
    pull.attrs["check-pending"] = []
    pull.attrs["check-success"] = ["ci-1"]
    pull.sync_checks()
    await assert_queue_rule_checks_status(conds, pull, check_api.Conclusion.SUCCESS)

    # half reported failure, UNDEFINED BEHAVIOR
    # FIXME(sileht): Why are we failing instead of waiting ci-2 ? (MRGFY-729)
    pull.attrs["check-failure"] = ["ci-1"]
    pull.attrs["check-pending"] = []
    pull.attrs["check-success"] = []
    pull.sync_checks()
    await assert_queue_rule_checks_status(conds, pull, check_api.Conclusion.FAILURE)


@pytest.mark.asyncio
async def test_rules_checks_status_expected_failure():
    pull = FakeQueuePullRequest(
        {
            "number": 1,
            "current-year": date.Year(2018),
            "author": "me",
            "base": "main",
            "head": "feature-1",
            "check-success": [],
            "check-failure": [],
            "check-pending": [],
            "check": [],
            "check-success-or-neutral-or-pending": [],
        }
    )
    conds = ["check-failure=ci-1"]

    # Nothing reported
    await assert_queue_rule_checks_status(conds, pull, check_api.Conclusion.PENDING)

    # Pending reported
    pull.attrs["check-pending"] = ["ci-1"]
    pull.attrs["check-failure"] = []
    pull.attrs["check-success"] = []
    pull.sync_checks()
    await assert_queue_rule_checks_status(conds, pull, check_api.Conclusion.PENDING)

    # Failure reported
    pull.attrs["check-pending"] = []
    pull.attrs["check-failure"] = ["ci-1"]
    pull.attrs["check-success"] = []
    pull.sync_checks()
    await assert_queue_rule_checks_status(conds, pull, check_api.Conclusion.SUCCESS)

    # Success reported
    # FIXME(sileht): we should fail! (MRGFY-730)
    pull.attrs["check-pending"] = []
    pull.attrs["check-failure"] = []
    pull.attrs["check-success"] = ["ci-1"]
    pull.sync_checks()
    await assert_queue_rule_checks_status(conds, pull, check_api.Conclusion.PENDING)


@pytest.mark.asyncio
async def test_rules_checks_status_regular():
    pull = FakeQueuePullRequest(
        {
            "number": 1,
            "current-year": date.Year(2018),
            "author": "me",
            "base": "main",
            "head": "feature-1",
            "check-success": [],
            "check-failure": [],
            "check-pending": [],
            "check": [],
            "check-success-or-neutral-or-pending": [],
        }
    )
    conds = ["check-success=ci-1", "check-success=ci-2"]

    # Nothing reported
    await assert_queue_rule_checks_status(conds, pull, check_api.Conclusion.PENDING)

    # Pending reported
    pull.attrs["check-pending"] = ["ci-1"]
    pull.attrs["check-failure"] = []
    pull.attrs["check-success"] = ["ci-2"]
    pull.sync_checks()
    await assert_queue_rule_checks_status(conds, pull, check_api.Conclusion.PENDING)

    # Failure reported
    pull.attrs["check-pending"] = []
    pull.attrs["check-failure"] = ["ci-1"]
    pull.attrs["check-success"] = ["ci-2"]
    pull.sync_checks()
    await assert_queue_rule_checks_status(conds, pull, check_api.Conclusion.FAILURE)

    # Success reported
    pull.attrs["check-pending"] = []
    pull.attrs["check-failure"] = []
    pull.attrs["check-success"] = ["ci-1", "ci-2"]
    pull.sync_checks()
    await assert_queue_rule_checks_status(conds, pull, check_api.Conclusion.SUCCESS)

    # half reported success
    pull.attrs["check-failure"] = []
    pull.attrs["check-pending"] = []
    pull.attrs["check-success"] = ["ci-1"]
    pull.sync_checks()
    await assert_queue_rule_checks_status(conds, pull, check_api.Conclusion.PENDING)

    # half reported failure, UNDEFINED BEHAVIOR
    # FIXME(sileht): Why are we waiting for ci-2 ? we can fail earlier (MRGFY-731)
    pull.attrs["check-failure"] = ["ci-1"]
    pull.attrs["check-pending"] = []
    pull.attrs["check-success"] = []
    pull.sync_checks()
    await assert_queue_rule_checks_status(conds, pull, check_api.Conclusion.PENDING)


@pytest.mark.asyncio
@freeze_time("2021-09-22T08:00:05", tz_offset=0)
async def test_rules_conditions_schedule():
    pulls = [
        FakeQueuePullRequest(
            {
                "number": 1,
                "author": "me",
                "base": "main",
                "current-timestamp": date.utcnow(),
                "current-time": date.utcnow(),
                "current-day": date.Day(22),
                "current-month": date.Month(9),
                "current-year": date.Year(2021),
                "current-day-of-week": date.DayOfWeek(3),
            }
        ),
    ]
    schema = voluptuous.Schema(
        voluptuous.All(
            [voluptuous.Coerce(rules.RuleConditionSchema)],
            voluptuous.Coerce(conditions.QueueRuleConditions),
        )
    )

    c = schema(
        [
            "base=main",
            "schedule=MON-FRI 08:00-17:00",
            "schedule=MONDAY-FRIDAY 10:00-12:00",
            "schedule=SAT-SUN 07:00-12:00",
        ]
    )

    await c(pulls)

    assert (
        c.get_summary()
        == """- [X] `base=main`
- [X] `schedule=MON-FRI 08:00-17:00`
- [ ] `schedule=MONDAY-FRIDAY 10:00-12:00`
- [ ] `schedule=SAT-SUN 07:00-12:00`
"""
    )