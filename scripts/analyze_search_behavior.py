"""Measure how agents used their search tools in stored Searchlight run bundles.

Reads one or more suite-run directories (each with a ``run-index.json``) and
reports, per harness and arm:

* search and fetch call counts, cells with no provider call, and cells that
  fetched pages without ever searching ("fetch-only");
* query style: share of natural-language queries, queries carrying a bare year,
  quoted phrases, ``site:`` operators and boolean operators, plus median length;
* which structured search parameters agents passed besides the query;
* with ``--catalog``, whether each task's primary-source URL appeared in any
  search result, and the pass rate with and without it (GAP brief bundles).

The classification rules are deliberately simple and stated in the output so
that others can audit or replace them. Nothing here makes network calls.

Usage:
    python3 scripts/analyze_search_behavior.py RUN_DIR [RUN_DIR ...]
        [--catalog catalogs/gap/tasks.yaml] [--json OUT.json]
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

SEARCH_TOOLS = frozenset({
    'brave_web_search', 'brave_llm_context', 'brave_news_search', 'brave_local_search',
    'brave_video_search', 'brave_image_search', 'brave_place_search',
    'tavily_search', 'tavily_research', 'web_search_exa', 'web_search_advanced_exa',
    'web_search', 'firecrawl_search', 'perplexity_search', 'perplexity_ask',
    'perplexity_research', 'perplexity_reason', 'WebSearch', 'codex_native_search', 'websearch',
})
FETCH_TOOLS = frozenset({
    'web_fetch_exa', 'tavily_extract', 'tavily_crawl', 'tavily_map', 'web_fetch',
    'firecrawl_scrape', 'firecrawl_crawl', 'firecrawl_map', 'WebFetch', 'codex_native_open',
    'brave_summarizer', 'webfetch',
})
# MCP server names an arm mounts its provider under. Hermes prefixes a tool with
# `mcp_<server>_` and Opencode with `<server>_`; Claude Code and Pi use
# `mcp__<server>__`, and Codex reports the server separately.
PROVIDER_SERVERS = ('exa', 'parallel', 'firecrawl', 'brave', 'tavily', 'perplexity')
SERVER_PREFIX = re.compile(r'^(?:mcp_)?(?:%s)_(.+)$' % '|'.join(PROVIDER_SERVERS))
FUNCTION_WORDS = frozenset(
    'the a an is are was were does do did how what which why when where who that this of to for '
    'with will should can about on from by as be has have'.split()
)
QUESTION_STARTS = frozenset(
    'how what which why when where who is are does do can should'.split()
)
RULES = {
    'natural_language': 'ends with "?", starts with an interrogative or auxiliary verb, '
                        'or contains at least two function words from a fixed list',
    'year': 'contains a token 2000-2099',
    'site_operator': 'contains "site:"',
    'boolean_operator': 'contains OR/AND (upper case), "after:" or "before:"',
    'search_call': 'a call to a search tool; each entry of a multi-query call (Parallel search_queries, '
                   'codex built-in search queries) and each user message in conversational inputs '
                   'counts as one query; system and assistant messages are excluded; codex built-in page views '
                   '(open_page, find_in_page, other) count as fetches',
    'fetch_only': 'the cell made at least one fetch call and no search call',
    'refused_call': 'a tool call the harness refused before it ran (Claude Code permission refusal); '
                    'refused calls still count as calls of their kind',
    'primary_source_surfaced': "the task's catalog oracle source_url (normalised: scheme, query, "
                               "fragment and trailing slash removed) appears among URLs in any search result; "
                               'measured only over cells that searched with observable returned results; '
                               'unknown if any search lacks results and no observed result contains the source',
    'tool_name': 'the provider tool name with any MCP server prefix removed (mcp__<server>__, Hermes mcp_<server>_, '
                 'Opencode <server>_); Opencode websearch/webfetch are its built-in search and fetch tools',
}


REFUSAL_MARKERS = ("haven't granted it yet", 'requested permissions to use')


def is_refusal(text: str) -> bool:
    return any(marker in text for marker in REFUSAL_MARKERS)


def normalise_url(url: str) -> str:
    url = re.sub(r'^https?://', '', url.strip().lower())
    return url.split('#')[0].split('?')[0].rstrip('/')


def query_style(query: str) -> dict:
    words = re.findall(r'[A-Za-z0-9][\w.\-]*', query)
    function_words = sum(1 for word in words if word.lower() in FUNCTION_WORDS)
    natural = (
        query.strip().endswith('?')
        or bool(words and words[0].lower() in QUESTION_STARTS)
        or function_words >= 2
    )
    return {
        'words': len(words),
        'natural_language': natural,
        'quoted': '"' in query,
        'site_operator': 'site:' in query.lower(),
        'year': bool(re.search(r'\b20\d\d\b', query)),
        'boolean_operator': bool(re.search(r'\b(OR|AND)\b|after:|before:', query)),
    }


def tool_name(name) -> str:
    name = str(name or '').split('__')[-1]
    if name in SEARCH_TOOLS or name in FETCH_TOOLS:
        return name
    match = SERVER_PREFIX.match(name)
    if match and (match.group(1) in SEARCH_TOOLS or match.group(1) in FETCH_TOOLS):
        return match.group(1)
    return name


def _arguments(value) -> dict:
    # Hermes records the model's tool arguments as the JSON string it emitted.
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return {}
    return value if isinstance(value, dict) else {}


def _text(content):
    if content is None or isinstance(content, str):
        return content
    return json.dumps(content)


def tool_events(transcript):
    """Yield (tool, arguments, result_text); unavailable results are None."""
    pending = {}
    for entry in transcript:
        if not isinstance(entry, dict):
            continue
        event = entry.get('harness_event')
        if not isinstance(event, dict):
            continue
        part = event.get('part')
        if event.get('type') == 'tool_use' and isinstance(part, dict):
            # Opencode reports each finished tool call as one event; a call that
            # errored exposes no results.
            state = part.get('state') if isinstance(part.get('state'), dict) else {}
            output = state.get('output') if state.get('status') == 'completed' else None
            yield tool_name(part.get('tool')), _arguments(state.get('input')), _text(output)
            continue
        if event.get('type') == 'tool_call':
            # Hermes: the call, then its result as a tool_result message part.
            pending[event.get('id')] = (tool_name(event.get('name')), _arguments(event.get('arguments')))
            continue
        item = event.get('item')
        if isinstance(item, dict) and event.get('type') == 'item.completed':
            if item.get('type') == 'mcp_tool_call':
                result = item.get('result') or {}
                text = ''
                if isinstance(result, dict):
                    text = ''.join(str(part.get('text', '')) for part in result.get('content') or []
                                   if isinstance(part, dict))
                yield item.get('tool'), item.get('arguments') or {}, text
            elif item.get('type') == 'web_search':
                # Codex's built-in web tool reports searches and page views as
                # one item type; only `search` actions are searches.
                action = item.get('action') if isinstance(item.get('action'), dict) else {}
                if action.get('type') == 'search':
                    queries = action.get('queries') or [action.get('query') or item.get('query') or '']
                    # Never treat the action/query (search inputs) as returned evidence.
                    result = next((item[key] for key in ('results', 'result') if key in item), None)
                    text = json.dumps(result) if result is not None else None
                    yield 'codex_native_search', {'search_queries': [q for q in queries if q]}, text
                else:
                    yield 'codex_native_open', {}, json.dumps(item)
            continue
        message = event.get('message')
        if not isinstance(message, dict):
            continue
        if message.get('role') == 'toolResult' and message.get('toolCallId') in pending:
            # Pi: a finished tool call is its own message (message_end only;
            # message_start/update repeat it while it streams).
            if event.get('type') == 'message_end':
                name, arguments = pending.pop(message.get('toolCallId'))
                yield name, arguments, None if message.get('isError') else _text(message.get('content'))
            continue
        for part in message.get('content') or []:
            if not isinstance(part, dict):
                continue
            if part.get('type') in ('tool_use', 'server_tool_use'):
                pending[part.get('id')] = (tool_name(part.get('name')), part.get('input') or {})
            elif part.get('type') == 'toolCall' and event.get('type') == 'message_end':
                pending[part.get('id')] = (tool_name(part.get('name')), _arguments(part.get('arguments')))
            elif part.get('type') in ('tool_result', 'web_search_tool_result') and part.get('tool_use_id') in pending:
                name, arguments = pending.pop(part.get('tool_use_id'))
                content = part.get('content')
                yield name, arguments, content if isinstance(content, str) else json.dumps(content)
    for name, arguments in pending.values():
        yield name, arguments, None


def queries_of(arguments) -> list[str]:
    if not isinstance(arguments, dict):
        return []
    if 'search_queries' in arguments:
        return [q for q in arguments.get('search_queries') or [] if isinstance(q, str)]
    query = arguments.get('query') or arguments.get('objective')
    if isinstance(query, str):
        return [query]
    messages = arguments.get('messages')
    if not isinstance(messages, list):
        return []
    return [message['content'] for message in messages
            if isinstance(message, dict) and message.get('role') == 'user'
            and isinstance(message.get('content'), str)]


def load_catalog(path: Path) -> dict:
    import yaml
    data = yaml.safe_load(path.read_text(encoding='utf-8'))
    tasks = data['tasks'] if isinstance(data, dict) else data
    return {task['id']: normalise_url(task['oracle']['source_url'])
            for task in tasks if isinstance(task.get('oracle'), dict) and task['oracle'].get('source_url')}


def analyse_cell(run_dir: Path, oracle: str | None) -> dict | None:
    transcript_path = run_dir / 'artifacts' / 'transcript.json'
    if not transcript_path.is_file():
        return None
    try:
        transcript = json.loads(transcript_path.read_text(encoding='utf-8'))
    except json.JSONDecodeError:
        return None
    searches, fetches, refused, queries, structured = 0, 0, 0, [], Counter()
    surfaced = False
    missing_results = False
    for name, arguments, text in tool_events(transcript):
        if (name in SEARCH_TOOLS or name in FETCH_TOOLS) and text is not None and is_refusal(text):
            refused += 1
        if name in SEARCH_TOOLS:
            searches += 1
            queries.extend(queries_of(arguments))
            if isinstance(arguments, dict):
                structured.update(key for key in arguments if key not in ('query', 'search_queries'))
            if oracle:
                missing_results = missing_results or text is None
                urls = {normalise_url(url) for url in re.findall(r'https?://[^\s"\'<>)\]\\]+', text or '')}
                surfaced = surfaced or oracle in urls
        elif name in FETCH_TOOLS:
            fetches += 1
    passed = None
    outcome_path = run_dir / 'evaluations' / 'gap-outcome.json'
    if outcome_path.is_file():
        outcome = json.loads(outcome_path.read_text(encoding='utf-8'))
        if outcome.get('status') == 'scored':
            passed = bool(outcome.get('passed'))
    return {
        'searches': searches, 'fetches': fetches, 'refused': refused, 'queries': [query_style(q) for q in queries],
        'structured': structured,
        'surfaced': (surfaced if surfaced or not missing_results else None) if searches and oracle else None,
        'passed': passed,
    }


def pct(numerator: int, denominator: int):
    return round(100 * numerator / denominator, 1) if denominator else None


def aggregate(cells: list[dict], with_oracle: bool) -> dict:
    styles = [style for cell in cells for style in cell['queries']]
    searched = [cell for cell in cells if cell['searches']]
    out = {
        'cells': len(cells),
        'search_calls': sum(cell['searches'] for cell in cells),
        'fetch_calls': sum(cell['fetches'] for cell in cells),
        'no_provider_call_cells': sum(1 for cell in cells if not cell['searches'] and not cell['fetches']),
        'fetch_only_cells': sum(1 for cell in cells if not cell['searches'] and cell['fetches']),
        'refused_calls': sum(cell['refused'] for cell in cells),
        'cells_with_refused_calls': sum(1 for cell in cells if cell['refused']),
        'queries': len(styles),
        'median_query_words': statistics.median([s['words'] for s in styles]) if styles else None,
        'structured_parameters': dict(sorted(sum((cell['structured'] for cell in cells), Counter()).items())),
    }
    for key in ('natural_language', 'year', 'quoted', 'site_operator', 'boolean_operator'):
        out[f'{key}_pct'] = pct(sum(1 for s in styles if s[key]), len(styles))
    if with_oracle:
        hit = [cell for cell in searched if cell['surfaced'] is True]
        miss = [cell for cell in searched if cell['surfaced'] is False]
        scored_hit = [cell for cell in hit if cell['passed'] is not None]
        scored_miss = [cell for cell in miss if cell['passed'] is not None]
        fetch_only = [cell for cell in cells if not cell['searches'] and cell['fetches'] and cell['passed'] is not None]
        out.update({
            'searched_cells': len(searched),
            'primary_source_observed_cells': len(hit) + len(miss),
            'primary_source_unknown_cells': len(searched) - len(hit) - len(miss),
            'primary_source_surfaced': len(hit),
            'pass_when_surfaced': [sum(cell['passed'] for cell in scored_hit), len(scored_hit)],
            'pass_when_not_surfaced': [sum(cell['passed'] for cell in scored_miss), len(scored_miss)],
            'pass_when_fetch_only': [sum(cell['passed'] for cell in fetch_only), len(fetch_only)],
            'pass_when_searched': [sum(cell['passed'] for cell in searched if cell['passed'] is not None),
                                   sum(1 for cell in searched if cell['passed'] is not None)],
        })
    return out


def analyse(run_dirs: list[Path], catalog: Path | None = None) -> dict:
    oracles = load_catalog(catalog) if catalog else {}
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for run_dir in run_dirs:
        index = json.loads((run_dir / 'run-index.json').read_text(encoding='utf-8'))
        for entry in index:
            arm = entry.get('provider_id')
            harness = entry.get('harness_id')
            cell_dir = Path(entry.get('run_dir') or run_dir / 'bundles' / entry.get('run_id', ''))
            if not cell_dir.is_absolute():
                cell_dir = run_dir / cell_dir
            if not arm or not harness:
                continue
            cell = analyse_cell(cell_dir, oracles.get(entry.get('task_id')))
            if cell is not None:
                groups[(harness, arm)].append(cell)
    rows = [{'harness': harness, 'arm': arm, **aggregate(cells, bool(oracles))}
            for (harness, arm), cells in sorted(groups.items())]
    all_styles = [s for cells in groups.values() for cell in cells for s in cell['queries']]
    return {
        'rules': RULES,
        'rows': rows,
        'totals': {
            'cells': sum(len(cells) for cells in groups.values()),
            'queries': len(all_styles),
            'natural_language_pct': pct(sum(s['natural_language'] for s in all_styles), len(all_styles)),
            'year_pct': pct(sum(s['year'] for s in all_styles), len(all_styles)),
            'site_operator_pct': pct(sum(s['site_operator'] for s in all_styles), len(all_styles)),
        },
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('run_dirs', nargs='+', type=Path, help='suite-run directories with run-index.json')
    parser.add_argument('--catalog', type=Path, help='GAP tasks.yaml for primary-source surfacing')
    parser.add_argument('--json', type=Path, help='write the full result as JSON')
    args = parser.parse_args(argv)
    result = analyse(args.run_dirs, args.catalog)
    if args.json:
        args.json.write_text(json.dumps(result, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    for row in result['rows']:
        print(f"{row['harness']:12} {row['arm']:13} cells={row['cells']:3} queries={row['queries']:4} "
              f"NL={row['natural_language_pct']}% site={row['site_operator_pct']}% "
              f"fetch-only={row['fetch_only_cells']}")
    totals = result['totals']
    print(f"total queries={totals['queries']} natural-language={totals['natural_language_pct']}%")
    return 0


if __name__ == '__main__':
    sys.exit(main())
