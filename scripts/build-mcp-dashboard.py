"""Build the inline MCP App using the web dashboard's styles and renderers."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def section(source, start, end):
    _, found, remaining = source.partition(start)
    if not found or end not in remaining:
        raise ValueError(f'Dashboard source markers missing: {start} / {end}')
    return remaining.split(end, 1)[0]


def build():
    source = (ROOT / 'index.html').read_text(encoding='utf-8')
    shell = (ROOT / 'mcp' / 'dashboard.html').read_text(encoding='utf-8')
    chart_scripts = '\n'.join(line for line in source.splitlines()
                              if '<script src="https://cdn.jsdelivr.net/npm/chart' in line)
    if not chart_scripts:
        raise ValueError('Dashboard chart scripts are missing.')
    return (shell.replace('<!-- chart libraries -->', chart_scripts)
            .replace('<!-- dashboard styles -->', section(source, '<style>', '</style>'))
            .replace('<!-- dashboard markup -->', section(source, '<!-- Shared dashboard start -->', '<!-- Shared dashboard end -->'))
            .replace('/* dashboard renderer */', section(source, '// Shared dashboard renderer start\n', '// Shared dashboard renderer end'))
            .replace('/* app bridge */', (ROOT / 'mcp' / 'dashboard.js').read_text(encoding='utf-8')))


if __name__ == '__main__':
    (ROOT / 'public' / 'mcp-dashboard.html').write_text(build(), encoding='utf-8')
