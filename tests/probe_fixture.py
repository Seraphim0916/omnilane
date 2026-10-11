"""Complete offline probe evidence shared by overlay and re-sign fixtures."""
import hashlib
import json
import probe
import probe_sweep


def write_probe(evidence, vendor, entry, verdict='pass'):
    record = dict(command=probe_sweep.command(vendor, entry['model'], entry['effort']),
                  exit_code=0, timed_out=False, probed_at='2026-01-01T00:00:00+00:00')
    token = probe_sweep.TOKENS[vendor]
    answer = token if verdict == 'pass' else 'probe response did not match'
    stdout = json.dumps(dict(result=answer, modelUsage={entry['model']: {}})) if vendor in ('claude', 'grok') else answer
    extra = {}
    if vendor in ('codex', 'gemini'):
        extra['rollout' if vendor == 'codex' else 'cli_log'] = 'model\t' + entry['model'] + '\n'
    result = probe.verdict(record, stdout, '', 'agy' if vendor == 'gemini' else vendor, token, extra)
    if result[0] != verdict:
        raise AssertionError(result)
    record.update(zip(('verdict', 'verdict_reason', 'observed_model', 'evidence_tier'), result))
    files = {}
    for suffix, content in dict(stdout=stdout, stderr='', json=json.dumps(record), **extra).items():
        path = evidence / (entry['name'] + '.' + suffix)
        path.write_text(content)
        files[suffix] = dict(path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    return files
