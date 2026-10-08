"""Opencode run --format json protocol (v1.17.3)."""
import json
from collections.abc import Mapping
from ..live_harness import HarnessProtocol, HarnessSummary
from .opencode import PROVIDER


def usage_row(tokens):
    if not isinstance(tokens, Mapping):
        return None
    cache = tokens.get("cache")
    if not isinstance(cache, Mapping):
        return None
    counts = [tokens.get(key) for key in ("input", "output", "reasoning")]
    counts += [cache.get(key) for key in ("read", "write")]
    if any(type(value) is not int or value < 0 for value in counts):
        return None
    row = {
        "input": tokens["input"] + cache["write"],
        "cached_input": cache["read"],
        "output": tokens["output"],
        "reasoning": tokens["reasoning"],
    }
    row["total_billable"] = sum(row.values())
    row["cache_write"] = cache["write"]
    return row


class OpencodeProtocol(HarnessProtocol):
    harness_id = "opencode"

    def argv(self, binary, config, last_message_path):
        model = config.model_id or ""
        if not model.startswith("litellm/"):
            raise ValueError("opencode requires an explicit litellm/<route> OSS model")
        return [binary, "run", "--pure", "--format", "json", "--model",
                f"{PROVIDER}/{model[len('litellm/'):]}", *config.harness_args]

    def is_ready(self, event):
        return event.get("type") in {"step_start", "step_finish", "text", "tool_use", "reasoning"}

    def is_output(self, event):
        return event.get("type") in {"text", "reasoning"}

    def running_usage(self, event, seen):
        part = event.get("part")
        part = part if isinstance(part, Mapping) else {}
        if event.get("type") == "step_finish":
            row = usage_row(part.get("tokens"))
            if row is not None:
                seen[str(part.get("id") or len(seen))] = row["total_billable"]

    def summarize(self, events, last_message):
        texts, rows, errors = {}, {}, []
        complete_usage = True
        for event in events:
            part = event.get("part")
            part = part if isinstance(part, Mapping) else {}
            if event.get("type") == "text" and isinstance(part.get("text"), str):
                message = str(part.get("messageID") or "final")
                texts.setdefault(message, {})[str(part.get("id") or len(texts[message]))] = part["text"]
            if event.get("type") == "step_finish":
                row = usage_row(part.get("tokens"))
                if row is not None:
                    rows[str(part.get("id") or len(rows))] = row
                else:
                    complete_usage = False
            if event.get("type") == "error":
                errors.append(json.dumps(event.get("error", "Opencode error")))
        usage = None
        if rows and complete_usage:
            usage = {key: sum(row[key] for row in rows.values()) for key in next(iter(rows.values()))}
        final = "\n".join(next(reversed(texts.values())).values()) if texts else None
        return HarnessSummary(None if errors else final,
                              usage, tuple(errors), bool(errors), False)
