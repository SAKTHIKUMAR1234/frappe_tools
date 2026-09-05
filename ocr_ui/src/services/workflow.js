// Presentation comes from the adapter, never a hard-coded DocType switch.
// A partial/older manifest must not make any extracted fact unreachable.
export function reviewSections(run) {
  const fields = new Map(
    (run.fields || []).map((field) => [field.fieldname, field]),
  );
  const tables = new Map(
    (run.tables || []).map((table) => [table.fieldname, table]),
  );
  const sections = [];
  const usedKeys = new Set(["checks", "matching"]);
  for (const spec of run.workflow?.review_sections || []) {
    if (!spec.key || usedKeys.has(spec.key)) continue;
    usedKeys.add(spec.key);
    if (spec.kind === "custom") {
      sections.push({ ...spec, fields: [], tables: [] });
    } else if (spec.kind === "table" && tables.has(spec.table)) {
      sections.push({ ...spec, tables: [tables.get(spec.table)], fields: [] });
      tables.delete(spec.table);
    } else if (spec.kind === "fields") {
      const selected = (spec.fields || [])
        .filter((name) => fields.has(name))
        .map((name) => {
          const field = fields.get(name);
          fields.delete(name);
          return field;
        });
      if (selected.length)
        sections.push({ ...spec, fields: selected, tables: [] });
    }
  }
  function fallback(key, label, remainingFields, remainingTables) {
    while (usedKeys.has(key)) key += "-extra";
    usedKeys.add(key);
    sections.push({
      key,
      label,
      fields: remainingFields,
      tables: remainingTables,
    });
  }
  if (fields.size)
    fallback(
      "details",
      sections.length ? "Other details" : "Details",
      [...fields.values()],
      [],
    );
  for (const table of tables.values())
    fallback(`table-${table.fieldname}`, table.label, [], [table]);
  return sections;
}

export function fieldEvidence(run, selected) {
  if (!selected) return null;
  if (selected.table && selected.row_no) {
    const row = run.tables
      ?.find((table) => table.fieldname === selected.table)
      ?.rows.find((item) => item.row_no === selected.row_no);
    if (!row) return null;
    const cell = selected.column ? row.cell_evidence?.[selected.column] : null;
    return {
      ...row,
      table: selected.table,
      column: selected.column,
      label: selected.label,
      source_page: cell?.page || row.source_page,
      bbox: cell?.x != null ? cell : row.bbox,
    };
  }
  return (
    run.fields?.find((field) => field.fieldname === selected.fieldname) ||
    run.issues?.find((issue) => issue.path && issue.path === selected.path) ||
    null
  );
}

// Only focus fields declared by this adapter. A row-level or cross-field check
// opens its section without pretending it belongs to an arbitrary input.
export function reviewIssueTarget(sections, issue) {
  const [kind, name, rowNumber, columnKey] = (issue.path || "").split(":");
  for (const section of sections) {
    if (kind === "field") {
      const field = section.fields.find((item) => item.fieldname === name);
      if (field) return { section: section.key, inputId: `field-${name}` };
    }
    if (kind === "table") {
      const table = section.tables.find((item) => item.fieldname === name);
      if (!table) continue;
      const row = table.rows?.find((item) => String(item.row_no) === rowNumber);
      const column = table.columns?.find((item) => item.key === columnKey);
      return {
        section: section.key,
        inputId:
          row && column ? `cell-${name}-${row.row_no}-${column.key}` : null,
      };
    }
  }
  return null;
}
