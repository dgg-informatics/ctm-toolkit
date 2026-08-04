// create_default_views.js — standard CTM views for a daily trials database.
//
// Views live per-database, so each new daily DB needs them recreated. This
// script runs against whatever DB the connection URI points at.
//
// USAGE (append the dated db name to the URI — that's the only thing that
// changes day to day):
//   mongosh "mongodb://localhost:27018/2026-08-05_dev" scripts/create_default_views.js
//
// Idempotent: drops each view if present, then recreates. Safe to re-run.

const TRIAL = "trial";
const TRIAL_MATCH = "trial_match";

function makeView(name, viewOn, pipeline) {
  db.getCollection(name).drop();            // no-op if it doesn't exist
  db.createView(name, viewOn, pipeline);
  print("  created view: " + name);
}

print("Building default views in DB: " + db.getName());

// ── trial_match views ────────────────────────────────────────────────────────

// All genomic matches. reason_type "genomic" is the higher-level discriminator,
// so this captures every genomic reason — gene-level muts, CNVs, fusions,
// specific protein-change ("variant") hits, MMR, and signatures — not just the
// match_type "gene" subset.
makeView("genomic_matches", TRIAL_MATCH, [
  { $match: { reason_type: "genomic" } }
]);

// Clinical matches (diagnosis/age/gender/ecog). "tmb" is a separate clinical
// match_type, not included here.
makeView("clinical_matches", TRIAL_MATCH, [
  { $match: { match_type: "generic_clinical" } }
]);

// Matches where the patient's cancer type specifically matched the trial.
makeView("specific_ct_matches", TRIAL_MATCH, [
  { $match: { cancer_type_match: "specific" } }
]);

// Strongest matches: specific cancer-type match AND a gene-level genomic hit.
makeView("top_matched", TRIAL_MATCH, [
  { $match: { cancer_type_match: "specific", match_type: "gene" } }
]);

// Breakdown: match count + distinct trial count per match_type, sorted.
makeView("match_type_breakdown", TRIAL_MATCH, [
  { $group: {
      _id: "$match_type",
      match_count: { $sum: 1 },
      trials: { $addToSet: { $ifNull: ["$nct_id", "$protocol_no"] } }
  } },
  { $project: { _id: 0, match_type: "$_id", match_count: 1, trial_count: { $size: "$trials" } } },
  { $sort: { match_count: -1 } }
]);

// One filtered view per distinct match_type value actually present in this DB
// (e.g. mt_gene, mt_variant, mt_generic_clinical, mt_tmb, mt_mmr, mt_signature).
// Generated dynamically so it adapts to whatever each day's data contains.
db.getCollection(TRIAL_MATCH).distinct("match_type").forEach(function (mt) {
  if (mt === null || mt === undefined) return;         // skip docs with no match_type
  const safe = String(mt).replace(/[^A-Za-z0-9]+/g, "_");
  makeView("mt_" + safe, TRIAL_MATCH, [{ $match: { match_type: mt } }]);
});

// ── trial views ──────────────────────────────────────────────────────────────

// Trials with at least one biomarker reference on file.
makeView("biomarker_trials", TRIAL, [
  { $match: { "_llm_curation.biomarker_references.0": { $exists: true } } }
]);

// Full trial docs for every trial that produced at least one trial_match
// (unique by construction — trial holds one doc per trial). Joins on nct_id OR
// protocol_no so it works for AMC (protocol_no) and West/Sparrow (nct_id only).
makeView("ct_matches", TRIAL, [
  { $lookup: {
      from: TRIAL_MATCH,
      let: { nct: "$nct_id", pno: "$protocol_no" },
      pipeline: [
        { $match: { $expr: { $or: [
            { $and: [ { $ne: ["$$nct", null] }, { $eq: ["$nct_id", "$$nct"] } ] },
            { $and: [ { $ne: ["$$pno", null] }, { $eq: ["$protocol_no", "$$pno"] } ] }
        ] } } },
        { $limit: 1 }
      ],
      as: "_m"
  } },
  { $match: { "_m.0": { $exists: true } } },
  { $project: { _m: 0 } }
]);

// ── Criteria-tier categorization ─────────────────────────────────────────────
// Mongo views can't recursively detect a diagnosis/genomic clause nested inside
// and/or wrappers, so we categorize each trial HERE in JS and bake the resulting
// id lists into the views as $in filters. Snapshot at run time — re-run if the
// trial set changes. Every trial stays in the collection; these only classify.
//
// Tiers (each trial lands in exactly one):
//   meaningful : has a genomic clause OR a SPECIFIC oncotree dx (not _SOLID_/_LIQUID_)
//   basket     : has an oncotree dx but only _SOLID_/_LIQUID_ (broad basket trials)
//   trivial    : non-empty match, but no dx and no genomic (age/gender/ecog only)
//   empty      : match[] is empty
const BROAD = new Set(["_SOLID_", "_LIQUID_"]);

function hasGenomic(node) {
  if (Array.isArray(node)) return node.some(hasGenomic);
  if (node && typeof node === "object") {
    if (node.genomic !== undefined) return true;
    if (node.and && hasGenomic(node.and)) return true;
    if (node.or && hasGenomic(node.or)) return true;
  }
  return false;
}
function collectDx(node, out) {
  if (Array.isArray(node)) { node.forEach(n => collectDx(n, out)); return; }
  if (node && typeof node === "object") {
    if (node.clinical && node.clinical.oncotree_primary_diagnosis !== undefined)
      out.push(node.clinical.oncotree_primary_diagnosis);
    if (node.and) collectDx(node.and, out);
    if (node.or) collectDx(node.or, out);
  }
}

const buckets = {
  meaningful: { protos: [], ncts: [], keys: new Set() },
  basket:     { protos: [], ncts: [], keys: new Set() },
  trivial:    { protos: [], ncts: [], keys: new Set() },
  empty:      { protos: [], ncts: [], keys: new Set() },
};
function add(bucket, t) {
  if (t.protocol_no != null) buckets[bucket].protos.push(t.protocol_no);
  if (t.nct_id != null) buckets[bucket].ncts.push(t.nct_id);
  buckets[bucket].keys.add(t.nct_id || t.protocol_no);   // unique-trial key
}

db.getCollection(TRIAL).find({}, { protocol_no: 1, nct_id: 1, "treatment_list.step.match": 1 }).forEach(function (t) {
  const steps = (t.treatment_list || {}).step || [];
  const m = steps.length ? (steps[0].match || []) : [];
  if (m.length === 0) { add("empty", t); return; }
  const dx = []; collectDx(m, dx);
  const specificDx = dx.some(d => !BROAD.has(d));
  const broadDx = dx.some(d => BROAD.has(d));
  if (hasGenomic(m) || specificDx) add("meaningful", t);
  else if (broadDx) add("basket", t);
  else add("trivial", t);
});

// membership match on either identifier (handles AMC protocol_no + West/Sparrow nct_id)
function keyMatch(bucket) {
  return { $match: { $or: [
    { protocol_no: { $in: buckets[bucket].protos } },
    { nct_id: { $in: buckets[bucket].ncts } }
  ] } };
}

print("Trial tiers (unique trials) — meaningful:" + buckets.meaningful.keys.size +
      " basket:" + buckets.basket.keys.size +
      " trivial:" + buckets.trivial.keys.size +
      " empty:" + buckets.empty.keys.size);

// trial views: keep every trial in the collection; these just classify.
makeView("trial_no_match_clause", TRIAL, [keyMatch("empty")]);
makeView("trial_trivial_criteria", TRIAL, [keyMatch("trivial")]);
makeView("trial_basket_solid_liquid", TRIAL, [keyMatch("basket")]);

// trial_match views: meaningful (specific dx or genomic) vs basket, separated;
// trivial + empty trials are excluded entirely from both.
makeView("trial_match_meaningful", TRIAL_MATCH, [keyMatch("meaningful")]);
makeView("trial_match_generic_oncotree", TRIAL_MATCH, [keyMatch("basket")]);

// Full trial docs for the unique meaningful-matched trials — i.e. one doc per
// trial behind trial_match_meaningful (the ~27), deduped even if the trial
// collection holds duplicate copies of a trial.
makeView("uniq_matches_full", TRIAL, [
  keyMatch("meaningful"),                                  // in the meaningful tier
  { $lookup: {
      from: TRIAL_MATCH,
      let: { nct: "$nct_id", pno: "$protocol_no" },
      pipeline: [
        { $match: { $expr: { $or: [
            { $and: [ { $ne: ["$$nct", null] }, { $eq: ["$nct_id", "$$nct"] } ] },
            { $and: [ { $ne: ["$$pno", null] }, { $eq: ["$protocol_no", "$$pno"] } ] }
        ] } } },
        { $limit: 1 }
      ],
      as: "_m"
  } },
  { $match: { "_m.0": { $exists: true } } },               // actually produced a match
  { $group: { _id: { $ifNull: ["$nct_id", "$protocol_no"] }, doc: { $first: "$$ROOT" } } },  // dedupe
  { $replaceRoot: { newRoot: "$doc" } },
  { $project: { _m: 0 } }
]);

print("Done.");
