"""Read Hermes 0.16.0 SQLite session evidence without importing Hermes."""
import json
import sqlite3
from pathlib import Path
from sew.live_harness import HarnessProtocol, HarnessSummary
from .hermes import usage_parser


class HermesProtocol(HarnessProtocol):
    harness_id = "hermes"

    def argv(self, binary, config, last_message_path):
        return [binary, "-z", config.prompt_text or "", "-m",
                (config.model_id or "").removeprefix("litellm/"),
                "--provider", "custom:searchlight", *config.harness_args]

    def prompt_argv(self, binary, config, last_message_path, prompt):
        argv = self.argv(binary, config, last_message_path)
        argv[2] = prompt
        return argv

    def poll_events(self, env, seen):
        path = Path(env["HERMES_HOME"]) / "state.db"
        if not path.is_file():
            return []
        events = []
        try:
            with sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, timeout=0.05) as db:
                db.row_factory = sqlite3.Row
                sessions = db.execute("SELECT id, input_tokens, output_tokens, cache_read_tokens, reasoning_tokens FROM sessions").fetchall()
                for row in sessions:
                    key = ("ready", row['id'])
                    if key not in seen:
                        seen.add(key)
                        events.append({"type": "hermes.ready", "session_id": row['id']})
                    usage = dict(row)
                    key = ("usage", json.dumps(usage, sort_keys=True))
                    if key not in seen:
                        seen.add(key)
                        events.append({"type": "hermes.usage", "usage": usage})
                for row in db.execute("SELECT id, role, content, tool_calls, tool_call_id FROM messages ORDER BY id"):
                    key = ("message", row['id'])
                    if key in seen:
                        continue
                    seen.add(key)
                    if row['role'] == 'assistant':
                        for call in json.loads(row['tool_calls'] or '[]'):
                            events.append({"type": "tool_call", "id": call.get('id'),
                                           "name": call['function']['name'],
                                           "arguments": call['function'].get('arguments')})
                        if row['content']:
                            events.append({"type": "hermes.assistant", "text": row['content']})
                    elif row['role'] == 'tool':
                        events.append({"type": "user", "message": {"content": [{
                            "type": "tool_result", "tool_use_id": row['tool_call_id'],
                            "content": row['content'],
                        }]}})
        except sqlite3.OperationalError:
            # Database creation/writes can race polling; retry on the next pass.
            return events
        except (sqlite3.Error, ValueError, KeyError, TypeError):
            events.append({"type": "hermes.error", "message": "Invalid Hermes session ledger"})
        return events

    def reset_session(self, env):
        """Drop a finished launch's ledger so the next launch reads only its own."""
        for name in ("state.db", "state.db-wal", "state.db-shm", "state.db-journal"):
            (Path(env["HERMES_HOME"]) / name).unlink(missing_ok=True)

    def is_ready(self, event):
        return event.get('type') == 'hermes.ready'

    def is_output(self, event):
        return event.get('type') == 'hermes.assistant'

    def running_usage(self, event, seen):
        if event.get('type') == 'hermes.usage':
            row = event['usage']
            seen[row['id']] = int(row['input_tokens'] or 0) + int(row['cache_read_tokens'] or 0) + int(row['output_tokens'] or 0)

    def summarize(self, events, last_message):
        text = None
        rows = {}
        for event in events:
            if event.get('type') == 'hermes.assistant':
                text = event['text']
            elif event.get('type') == 'hermes.usage':
                rows[event['usage']['id']] = usage_parser(event['usage'])
        usage = {key: sum(row[key] for row in rows.values()) for key in ('input', 'output', 'cached_input', 'reasoning')} if rows else None
        errors = tuple(e["message"] for e in events if e.get("type") == "hermes.error")
        return HarnessSummary(None if errors else text, usage, errors, bool(errors), False)
