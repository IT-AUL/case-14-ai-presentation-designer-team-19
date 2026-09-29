export const FEATURE_PATHS = {
  planOnly: ['features.plan_only', 'generation.plan_only', 'plan_only'],
  repairDryRun: ['features.repair_dry_run', 'repair.dry_run'],
  pngPreviews: ['features.png_previews', 'previews.slide_png', 'slide_previews'],
  pdfAfterRepair: ['features.pdf_after_repair'],
  modelAuto: ['features.model_auto', 'model_auto'],
  ruleCatalog: ['features.audit_rules', 'features.rule_catalog', 'features.audit_rule_catalog'],
} as const satisfies Record<string, readonly string[]>
