export function HelloWorldRoute() {
  return (
    <div className="route-page-scroll space-y-6">
      <div>
        <p className="text-sm font-medium uppercase tracking-wide text-text-muted">Odoo TUI</p>
        <h1 className="text-2xl font-bold text-text">Hello World</h1>
      </div>
      <div className="rounded-lg border border-border bg-surface-raised p-4">
        <p className="text-text">The external Mission Control plugin is loaded correctly.</p>
      </div>
    </div>
  );
}
