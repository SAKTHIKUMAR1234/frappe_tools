const same = (a, b) => String(a ?? "") === String(b ?? "");

// Retain unsaved edits when another field is confirmed, a poll refreshes the
// record, or the operator switches sections. Accepted server values rebase it.
export function mergeDraft(store, key, incoming) {
  const current = store[key];
  const previous = current?.server || {};
  const values = current?.values || {};
  for (const [name, value] of Object.entries(incoming)) {
    if (!Object.hasOwn(previous, name) || same(values[name], previous[name]))
      values[name] = value;
  }
  store[key] = { values, server: { ...incoming } };
  return values;
}

export function hasUnsavedDrafts(store) {
  return Object.values(store).some(({ values, server }) =>
    Object.entries(server).some(([key, value]) => !same(values[key], value)),
  );
}
