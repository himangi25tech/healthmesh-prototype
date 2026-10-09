# Copyright (c) Meta Platforms, Inc. and affiliates.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Muse HealthMesh hub prototype.

A software-only prototype of the HealthMesh Pi-hub safety core: synthetic
vitals, a medication schedule, a deterministic rule-based escalation engine,
and an outbox shim standing in for ``musegadget send-user-msg``.

Run from ``~/workspace/muse-gadget-sdk/linux``::

    python3 -m healthmesh.hub --scenario normal-day --speed 60
"""

__all__ = ["vitals", "meds", "rules", "outbox", "hub"]
