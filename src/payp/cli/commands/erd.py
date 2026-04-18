"""Slash command: /erd - interactive visual ERD in browser (zero AI tokens).

Reads cached schema metadata (t1 + fk_graph), fetches columns in bulk
(2 SQL queries total), serializes as JSON, and opens an interactive D3.js
force-directed ERD in the default browser.

Features: drag tables, zoom/pan, click to focus, search, FK edge routing.
"""

from __future__ import annotations

import json
import logging
import tempfile
import webbrowser
from pathlib import Path
from typing import TYPE_CHECKING, Any

from payp.cli.runtime import _run_async
from payp.cli.state import _state, console

if TYPE_CHECKING:
    from payp.db.connection import ConnectionManager
    from payp.models import SchemaCatalog, SchemaGraph

logger = logging.getLogger(__name__)

_MAX_TABLES_WARN = 80


# -- Public command entry point --


def _cmd_erd(args: str) -> None:
    """Generate interactive visual ERD and open in browser."""
    from payp.models import SchemaGraph
    from payp.ui.theme import Color

    conn: Any = _state.get("connection_manager")
    if not conn or not conn.is_connected:
        console.print("[yellow]Connect to a database first with /db[/yellow]")
        return

    t1: SchemaCatalog | None = _state.get("t1")
    fk_graph: SchemaGraph = _state.get("fk_graph") or SchemaGraph()

    if not t1 or not t1.tables:
        console.print(
            "[yellow]No schema data. Run /schema --refresh first.[/yellow]"
        )
        return

    total = sum(len(tbls) for tbls in t1.tables.values())
    if total > _MAX_TABLES_WARN:
        console.print(
            f"[yellow]Large schema ({total} tables) - "
            f"the diagram may take a moment to render.[/yellow]"
        )

    with console.status("Building ERD..."):
        columns, pks = _run_async(_fetch_erd_data(conn, t1))
        fk_set = _fk_lookup(fk_graph)
        data = _build_data(t1, columns, pks, fk_set, fk_graph)

        conn_name = _state.get("active_connection", "database")
        data["meta"] = {
            "connName": conn_name,
            "dbType": conn.profile.db_type.value,
            "tableCount": total,
            "relCount": len(fk_graph.edges),
        }

        html = _build_html(data)
        tmp = Path(tempfile.gettempdir()) / f"payp_erd_{conn_name}.html"
        tmp.write_text(html, encoding="utf-8")

    webbrowser.open(tmp.as_uri())
    console.print(
        f"  [{Color.BRAND_ALT}]ERD opened in browser[/{Color.BRAND_ALT}] "
        f"[dim]({total} tables, {len(fk_graph.edges)} relationships)[/dim]"
    )
    console.print(f"  [dim]{tmp}[/dim]")


# -- Bulk data fetching (2 queries total, not per-table) --


async def _fetch_erd_data(
    conn: ConnectionManager,
    t1: SchemaCatalog,
) -> tuple[dict[str, list[dict[str, str]]], dict[str, set[str]]]:
    """Return (columns_by_table, pk_columns_by_table) in bulk."""
    from payp.models import DbType

    schemas = list(t1.tables.keys())
    if not schemas:
        return {}, {}

    if conn.db_type == DbType.POSTGRESQL:
        cols = await _cols_pg(conn, schemas)
        pks = await _pks_pg(conn, schemas)
    elif conn.db_type == DbType.MYSQL:
        cols = await _cols_mysql(conn, schemas)
        pks = await _pks_mysql(conn, schemas)
    elif conn.db_type == DbType.ORACLE:
        cols = await _cols_oracle(conn, schemas)
        pks = await _pks_oracle(conn, schemas)
    else:
        return {}, {}

    return cols, pks


# -- PostgreSQL --

async def _cols_pg(
    conn: ConnectionManager, schemas: list[str],
) -> dict[str, list[dict[str, str]]]:
    ph = ", ".join(["%s"] * len(schemas))
    rows = await conn.execute_raw(f"""
        SELECT table_schema, table_name, column_name,
               data_type, character_maximum_length
        FROM information_schema.columns
        WHERE table_schema IN ({ph})
        ORDER BY table_schema, table_name, ordinal_position
    """, tuple(schemas))
    return _group_cols(rows)


async def _pks_pg(
    conn: ConnectionManager, schemas: list[str],
) -> dict[str, set[str]]:
    ph = ", ".join(["%s"] * len(schemas))
    rows = await conn.execute_raw(f"""
        SELECT tc.table_schema, tc.table_name, kcu.column_name
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
          ON tc.constraint_name = kcu.constraint_name
         AND tc.table_schema = kcu.table_schema
        WHERE tc.table_schema IN ({ph})
          AND tc.constraint_type = 'PRIMARY KEY'
    """, tuple(schemas))
    return _group_pks(rows)


# -- MySQL --

async def _cols_mysql(
    conn: ConnectionManager, schemas: list[str],
) -> dict[str, list[dict[str, str]]]:
    ph = ", ".join(["%s"] * len(schemas))
    rows = await conn.execute_raw(f"""
        SELECT table_schema, table_name, column_name,
               data_type, character_maximum_length
        FROM information_schema.columns
        WHERE table_schema IN ({ph})
        ORDER BY table_schema, table_name, ordinal_position
    """, tuple(schemas))
    return _group_cols(rows)


async def _pks_mysql(
    conn: ConnectionManager, schemas: list[str],
) -> dict[str, set[str]]:
    ph = ", ".join(["%s"] * len(schemas))
    rows = await conn.execute_raw(f"""
        SELECT tc.table_schema, tc.table_name, kcu.column_name
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
          ON tc.constraint_name = kcu.constraint_name
         AND tc.table_schema = kcu.table_schema
         AND tc.table_name = kcu.table_name
        WHERE tc.table_schema IN ({ph})
          AND tc.constraint_type = 'PRIMARY KEY'
    """, tuple(schemas))
    return _group_pks(rows)


# -- Oracle --

async def _cols_oracle(
    conn: ConnectionManager, schemas: list[str],
) -> dict[str, list[dict[str, str]]]:
    upper = [s.upper() for s in schemas]
    ph = ", ".join(["%s"] * len(upper))
    rows = await conn.execute_raw(f"""
        SELECT owner AS table_schema, table_name, column_name,
               data_type, char_length AS character_maximum_length
        FROM all_tab_columns
        WHERE owner IN ({ph})
        ORDER BY owner, table_name, column_id
    """, tuple(upper))
    return _group_cols(rows)


async def _pks_oracle(
    conn: ConnectionManager, schemas: list[str],
) -> dict[str, set[str]]:
    upper = [s.upper() for s in schemas]
    ph = ", ".join(["%s"] * len(upper))
    rows = await conn.execute_raw(f"""
        SELECT ac.owner AS table_schema, ac.table_name,
               acc.column_name
        FROM all_constraints ac
        JOIN all_cons_columns acc
          ON ac.constraint_name = acc.constraint_name
         AND ac.owner = acc.owner
        WHERE ac.owner IN ({ph})
          AND ac.constraint_type = 'P'
    """, tuple(upper))
    return _group_pks(rows)


# -- Row grouping helpers --


def _group_cols(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, str]]]:
    """Group column rows into {schema.table: [{name, type}, ...]}."""
    result: dict[str, list[dict[str, str]]] = {}
    for r in rows:
        key = f"{r['table_schema']}.{r['table_name']}"
        if key not in result:
            result[key] = []
        result[key].append({
            "name": r["column_name"],
            "type": _simplify_type(
                r["data_type"], r.get("character_maximum_length"),
            ),
        })
    return result


def _group_pks(rows: list[dict[str, Any]]) -> dict[str, set[str]]:
    """Group PK rows into {schema.table: {col_name, ...}}."""
    result: dict[str, set[str]] = {}
    for r in rows:
        key = f"{r['table_schema']}.{r['table_name']}"
        if key not in result:
            result[key] = set()
        result[key].add(r["column_name"])
    return result


# -- FK lookup from cached graph --


def _fk_lookup(fk_graph: SchemaGraph) -> set[tuple[str, str]]:
    """Build a set of (schema.table, column) pairs that are FK columns."""
    result: set[tuple[str, str]] = set()
    for from_table, from_col, _to_table, _to_col in fk_graph.edges:
        result.add((from_table, from_col))
    return result


# -- JSON data builder --


def _build_data(
    t1: SchemaCatalog,
    columns: dict[str, list[dict[str, str]]],
    pks: dict[str, set[str]],
    fk_set: set[tuple[str, str]],
    fk_graph: SchemaGraph,
) -> dict[str, Any]:
    """Build JSON-serializable data for the D3 ERD."""
    single_schema = len(t1.tables) == 1
    tables: list[dict[str, Any]] = []
    table_idx: dict[str, int] = {}

    for schema, tbls in t1.tables.items():
        for table in sorted(tbls):
            full = f"{schema}.{table}"
            cols = columns.get(full, [])
            pk_cols = pks.get(full, set())

            col_list = []
            for c in cols:
                col_list.append({
                    "name": c["name"],
                    "type": c["type"],
                    "pk": c["name"] in pk_cols,
                    "fk": (full, c["name"]) in fk_set,
                })

            display = table if single_schema else f"{schema}.{table}"
            table_idx[full] = len(tables)
            tables.append({
                "id": full,
                "name": display,
                "columns": col_list,
            })

    edges: list[dict[str, Any]] = []
    for from_table, from_col, to_table, to_col in fk_graph.edges:
        if from_table not in table_idx or to_table not in table_idx:
            continue
        src = tables[table_idx[from_table]]
        tgt = tables[table_idx[to_table]]
        from_i = next(
            (i for i, c in enumerate(src["columns"]) if c["name"] == from_col),
            0,
        )
        to_i = next(
            (i for i, c in enumerate(tgt["columns"]) if c["name"] == to_col),
            0,
        )
        edges.append({
            "source": from_table,
            "target": to_table,
            "fromCol": from_col,
            "toCol": to_col,
            "fromIdx": from_i,
            "toIdx": to_i,
        })

    return {"tables": tables, "edges": edges}


# -- SQL type simplification --

_TYPE_MAP: dict[str, str] = {
    "integer": "int",
    "int": "int",
    "int4": "int",
    "int8": "bigint",
    "bigint": "bigint",
    "smallint": "smallint",
    "tinyint": "tinyint",
    "serial": "serial",
    "bigserial": "bigserial",
    "character varying": "varchar",
    "character": "char",
    "text": "text",
    "boolean": "bool",
    "timestamp without time zone": "timestamp",
    "timestamp with time zone": "timestamptz",
    "datetime": "datetime",
    "date": "date",
    "time without time zone": "time",
    "time with time zone": "timetz",
    "numeric": "decimal",
    "decimal": "decimal",
    "double precision": "double",
    "real": "float",
    "float": "float",
    "json": "json",
    "jsonb": "jsonb",
    "uuid": "uuid",
    "bytea": "bytes",
    "array": "array",
    "user-defined": "enum",
    "number": "number",
    "varchar2": "varchar",
    "nvarchar2": "nvarchar",
    "clob": "clob",
    "blob": "blob",
}


def _simplify_type(data_type: str, max_len: Any = None) -> str:
    """Map verbose SQL types to short ERD labels."""
    dt = data_type.lower().strip()
    short = _TYPE_MAP.get(dt, dt)
    if short == "varchar" and max_len:
        return f"varchar({max_len})"
    return short


# -- HTML builder --


def _build_html(data: dict[str, Any]) -> str:
    """Inject JSON data into the interactive ERD HTML page."""
    data_json = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return _HTML_TEMPLATE.replace("__DATA_PLACEHOLDER__", data_json)


# -- HTML + D3.js template --
# Uses __DATA_PLACEHOLDER__ as the single injection point.
# No Python .format() to avoid conflicts with JS/CSS braces.

_HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>payp ERD</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#0d1117;color:#c9d1d9;font-family:-apple-system,BlinkMacSystemFont,
  'Segoe UI',Helvetica,Arial,sans-serif;overflow:hidden}
header{position:fixed;top:0;left:0;right:0;z-index:10;height:48px;padding:0 20px;
  background:#161b22;border-bottom:1px solid #30363d;display:flex;align-items:center;gap:16px}
header h1{font-size:15px;font-weight:600;white-space:nowrap;margin:0;
  letter-spacing:0.3px}
header h1 a{text-decoration:none;
  background:linear-gradient(90deg,#A8006F 0%,#B4E04C 100%);
  -webkit-background-clip:text;background-clip:text;
  -webkit-text-fill-color:transparent;color:transparent;
  transition:filter .15s}
header h1 a:hover{filter:brightness(1.25) saturate(1.1)}
header h1 a:focus-visible{outline:1px solid #B4E04C;outline-offset:3px;border-radius:2px}
.meta{font-size:12px;color:#8b949e;white-space:nowrap}
#search{background:#0d1117;border:1px solid #30363d;border-radius:6px;color:#c9d1d9;
  padding:4px 10px;font-size:12px;width:200px;outline:none;transition:border-color .15s}
#search:focus{border-color:#A8006F}
.hint{font-size:11px;color:#484f58;margin-left:auto;white-space:nowrap}
svg{position:fixed;top:48px;left:0}
.edge{fill:none;stroke:#A8006F;stroke-width:1.5;stroke-opacity:0.4}
.edge.highlight{stroke:#B4E04C;stroke-opacity:1;stroke-width:2.5}
.edge.dim{stroke-opacity:0.08}
.card-bg{fill:#161b22;stroke:#30363d;stroke-width:1;rx:6}
.card-bg.highlight{stroke:#B4E04C;stroke-width:2}
.card-bg.dim{opacity:0.2}
.card-header{fill:url(#pp-hdr-grad);rx:6}
.col-row-bg{fill:transparent}
.col-row-bg:hover{fill:rgba(168,0,111,0.12)}
.col-name{fill:#c9d1d9;font-size:12px}
.col-type{fill:#8b949e;font-size:11px}
.badge-pk{fill:#B4E04C;font-size:9px;font-weight:700}
.badge-fk{fill:#A8006F;font-size:9px;font-weight:700}
.header-text{fill:#fff;font-size:13px;font-weight:600}
.table-group{cursor:grab}
.table-group:active{cursor:grabbing}
.table-group.dim .card-bg{opacity:0.15}
.table-group.dim .card-header{opacity:0.15}
.table-group.dim text{opacity:0.1}
.table-group.dim .col-row-bg{pointer-events:none}
.marker-one{fill:none;stroke:#A8006F;stroke-width:1.5}
.marker-many{fill:#A8006F;stroke:none}
</style>
</head>
<body>
<header>
  <h1><a href="https://payp-cli.com" target="_blank" rel="noopener noreferrer">payp ERD</a></h1>
  <span class="meta" id="meta"></span>
  <input type="text" id="search" placeholder="Search tables..." autocomplete="off">
  <span class="hint">Drag &middot; Scroll zoom &middot; Click focus &middot; Esc reset</span>
</header>
<svg id="canvas"></svg>
<script src="https://d3js.org/d3.v7.min.js"></script>
<script>
const DATA = __DATA_PLACEHOLDER__;

// --- Constants ---
const HEADER_H = 30;
const COL_H = 24;
const PAD_X = 10;
const PAD_Y = 6;
const MIN_W = 180;
const CHAR_W = 7.2;
const BADGE_W = 22;

// --- Setup ---
const W = window.innerWidth;
const H = window.innerHeight - 48;

document.getElementById('meta').textContent =
  DATA.meta.connName + ' \u00b7 ' + DATA.meta.dbType + ' \u00b7 ' +
  DATA.meta.tableCount + ' tables \u00b7 ' + DATA.meta.relCount + ' relationships';

const svg = d3.select('#canvas').attr('width', W).attr('height', H);

// Brand gradient reused by every table-card header (per-card object bounding box).
const defs = svg.append('defs');
const hdrGrad = defs.append('linearGradient')
  .attr('id', 'pp-hdr-grad')
  .attr('x1', '0%').attr('y1', '0%').attr('x2', '100%').attr('y2', '0%');
hdrGrad.append('stop').attr('offset', '0%').attr('stop-color', '#A8006F');
hdrGrad.append('stop').attr('offset', '100%').attr('stop-color', '#B4E04C');

const g = svg.append('g');

// Zoom
const zoom = d3.zoom().scaleExtent([0.05, 4])
  .on('zoom', e => g.attr('transform', e.transform));
svg.call(zoom);

// --- Compute card sizes ---
DATA.tables.forEach(t => {
  let maxName = t.name.length;
  let maxRight = 0;
  t.columns.forEach(c => {
    const nl = c.name.length + (c.pk || c.fk ? 3 : 0);
    if (nl > maxName) maxName = nl;
    if (c.type.length > maxRight) maxRight = c.type.length;
  });
  t.w = Math.max(MIN_W, (maxName + maxRight + 4) * CHAR_W + PAD_X * 2 + BADGE_W);
  t.h = HEADER_H + t.columns.length * COL_H + PAD_Y;
});

// Initial positions: circle layout
const n = DATA.tables.length;
const radius = Math.max(200, Math.sqrt(n) * 120);
DATA.tables.forEach((t, i) => {
  const a = (2 * Math.PI * i) / n;
  t.x = W / 2 + Math.cos(a) * radius;
  t.y = H / 2 + Math.sin(a) * radius;
});

// --- Force simulation (fast settle, no float) ---
const sim = d3.forceSimulation(DATA.tables)
  .force('charge', d3.forceManyBody().strength(-600))
  .force('center', d3.forceCenter(W / 2, H / 2).strength(0.05))
  .force('collide', d3.forceCollide().radius(d => Math.max(d.w, d.h) * 0.6 + 30))
  .force('link', d3.forceLink(DATA.edges).id(d => d.id).distance(250).strength(0.3))
  .alphaDecay(0.05)
  .velocityDecay(0.6);

// --- Draw edges (below cards) ---
const edgeG = g.append('g');
const edges = edgeG.selectAll('path').data(DATA.edges).join('path').attr('class', 'edge');

// --- Draw table cards ---
const cardG = g.append('g');
const cards = cardG.selectAll('g').data(DATA.tables).join('g')
  .attr('class', 'table-group')
  .call(d3.drag()
    .on('start', (e,d)=>{d.fx=d.x;d.fy=d.y;})
    .on('drag', (e,d)=>{d.fx=e.x;d.fy=e.y;d.x=e.x;d.y=e.y;tick();})
    .on('end', (e,d)=>{d.fx=d.x;d.fy=d.y;})
  );

// Card background
cards.append('rect').attr('class', 'card-bg')
  .attr('width', d => d.w).attr('height', d => d.h);

// Header background (top rounded, bottom square via clipPath)
cards.append('rect').attr('class', 'card-header')
  .attr('width', d => d.w).attr('height', HEADER_H);
// Square-off bottom corners of header (reuses the brand gradient).
cards.append('rect')
  .attr('y', HEADER_H - 6).attr('width', d => d.w).attr('height', 6)
  .attr('fill', 'url(#pp-hdr-grad)');

// Header text
cards.append('text').attr('class', 'header-text')
  .attr('x', PAD_X).attr('y', HEADER_H / 2)
  .attr('dominant-baseline', 'central')
  .text(d => d.name);

// Column rows
cards.each(function(d) {
  const card = d3.select(this);
  d.columns.forEach((col, i) => {
    const y = HEADER_H + i * COL_H;

    // Hover background
    card.append('rect').attr('class', 'col-row-bg')
      .attr('y', y).attr('width', d.w).attr('height', COL_H)
      .datum({tableId: d.id, col: col.name, idx: i});

    // PK/FK badge
    if (col.pk) {
      card.append('text').attr('class', 'badge-pk')
        .attr('x', PAD_X).attr('y', y + COL_H / 2)
        .attr('dominant-baseline', 'central').text('PK');
    } else if (col.fk) {
      card.append('text').attr('class', 'badge-fk')
        .attr('x', PAD_X).attr('y', y + COL_H / 2)
        .attr('dominant-baseline', 'central').text('FK');
    }

    // Column name
    card.append('text').attr('class', 'col-name')
      .attr('x', PAD_X + BADGE_W).attr('y', y + COL_H / 2)
      .attr('dominant-baseline', 'central').text(col.name);

    // Column type (right-aligned)
    card.append('text').attr('class', 'col-type')
      .attr('x', d.w - PAD_X).attr('y', y + COL_H / 2)
      .attr('dominant-baseline', 'central').attr('text-anchor', 'end')
      .text(col.type);
  });
});

// --- Edge path computation ---
function edgePath(d) {
  const s = d.source, t = d.target;
  const sy = s.y - s.h/2 + HEADER_H + d.fromIdx * COL_H + COL_H/2;
  const ty = t.y - t.h/2 + HEADER_H + d.toIdx * COL_H + COL_H/2;
  let sx, tx;
  if (s.x <= t.x) { sx = s.x + s.w/2; tx = t.x - t.w/2; }
  else             { sx = s.x - s.w/2; tx = t.x + t.w/2; }
  const gap = Math.abs(sx - tx);
  const off = Math.max(50, gap * 0.4);
  const cx1 = sx + (sx < tx ? off : -off);
  const cx2 = tx + (sx < tx ? -off : off);
  return 'M'+sx+','+sy+' C'+cx1+','+sy+' '+cx2+','+ty+' '+tx+','+ty;
}

// --- Tick ---
function tick() {
  cards.attr('transform', d => 'translate('+(d.x-d.w/2)+','+(d.y-d.h/2)+')');
  edges.attr('d', edgePath);
}
sim.on('tick', tick);

// --- Interactions ---

// Click table to focus
let focused = null;
cards.on('click', function(e, d) {
  e.stopPropagation();
  if (focused === d.id) { resetFocus(); return; }
  focused = d.id;
  const connected = new Set();
  connected.add(d.id);
  DATA.edges.forEach(edge => {
    const sid = typeof edge.source === 'object' ? edge.source.id : edge.source;
    const tid = typeof edge.target === 'object' ? edge.target.id : edge.target;
    if (sid === d.id) connected.add(tid);
    if (tid === d.id) connected.add(sid);
  });
  cards.classed('dim', t => !connected.has(t.id));
  cards.select('.card-bg').classed('highlight', t => t.id === d.id);
  edges.classed('highlight', edge => {
    const sid = typeof edge.source === 'object' ? edge.source.id : edge.source;
    const tid = typeof edge.target === 'object' ? edge.target.id : edge.target;
    return sid === d.id || tid === d.id;
  });
  edges.classed('dim', edge => {
    const sid = typeof edge.source === 'object' ? edge.source.id : edge.source;
    const tid = typeof edge.target === 'object' ? edge.target.id : edge.target;
    return sid !== d.id && tid !== d.id;
  });
});

svg.on('click', resetFocus);
document.addEventListener('keydown', e => { if (e.key === 'Escape') resetFocus(); });

function resetFocus() {
  focused = null;
  cards.classed('dim', false);
  cards.select('.card-bg').classed('highlight', false);
  edges.classed('highlight', false).classed('dim', false);
}

// Hover FK column -> highlight that edge
cardG.selectAll('.col-row-bg').on('mouseenter', function(e, rd) {
  edges.classed('highlight', edge => {
    const sid = typeof edge.source === 'object' ? edge.source.id : edge.source;
    const tid = typeof edge.target === 'object' ? edge.target.id : edge.target;
    return (sid === rd.tableId && edge.fromCol === rd.col) ||
           (tid === rd.tableId && edge.toCol === rd.col);
  });
}).on('mouseleave', function() {
  if (!focused) edges.classed('highlight', false);
});

// --- Search ---
const searchInput = document.getElementById('search');
searchInput.addEventListener('input', function() {
  const q = this.value.toLowerCase().trim();
  if (!q) { resetFocus(); return; }
  const matches = new Set();
  DATA.tables.forEach(t => { if (t.name.toLowerCase().includes(q)) matches.add(t.id); });
  cards.classed('dim', t => !matches.has(t.id));
  edges.classed('dim', edge => {
    const sid = typeof edge.source === 'object' ? edge.source.id : edge.source;
    const tid = typeof edge.target === 'object' ? edge.target.id : edge.target;
    return !matches.has(sid) && !matches.has(tid);
  });
});

// --- Freeze all nodes after simulation settles ---
sim.on('end', () => {
  DATA.tables.forEach(d => { d.fx = d.x; d.fy = d.y; });
});
</script>
</body>
</html>"""
