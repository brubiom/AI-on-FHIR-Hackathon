/*
 * Sample data so the prototype opens straight from disk (file://) with no
 * server and no API key. These mirror the two contracts the real tool consumes:
 *   window.SAMPLE_SOURCE_METADATA  — shape of source_metadata.json (the app)
 *   window.SAMPLE_STRUCTURE_MAP    — shape of structure_map.json   (the agent)
 * The content reflects the "disparate providers" Postgres source in
 * src/source_db (adt / lis / pacs / ris schemas) mapped to Bupa Chile Core.
 * Replace by dropping real JSON files in the Source tab.
 */

window.SAMPLE_SOURCE_METADATA = {
  engine: "postgresql",
  database: "provider",
  guide: { package: "hl7.fhir.cl.clcore", version: "1.9.3", indexed: true },
  schemas: ["adt", "lis", "pacs", "ris", "integration"],
  tables: [
    { name: "adt.person", rows: 120, role: "entity" },
    { name: "adt.encounter", rows: 340, role: "entity" },
    { name: "adt.code_value", rows: 122, role: "lookup" },
    { name: "adt.code_value_alias", rows: 106, role: "lookup" },
    { name: "lis.result", rows: 93, role: "entity" },
    { name: "lis.test_catalog", rows: 41, role: "lookup" },
    { name: "lis.dictionary", rows: 52, role: "lookup" },
    { name: "pacs.instance", rows: 1184, role: "entity" },
    { name: "pacs.series", rows: 26, role: "entity" },
    { name: "integration.lookup_table", rows: 42, role: "lookup" }
  ],
  candidate_entities: ["Patient", "Encounter", "Observation"],
  lookups: ["adt.code_value", "lis.test_catalog", "integration.lookup_table"]
};

// Light per-column profiles so the detail panel can show source facts.
window.SAMPLE_COLUMN_PROFILES = {
  "adt.person.mrn": { sql_type: "varchar(12)", nullable: false, samples: ["MRN0010432", "MRN0010588"] },
  "adt.person.family_name": { sql_type: "varchar(60)", nullable: false, samples: ["Rojas", "Fuentes"] },
  "adt.person.birth_date": { sql_type: "date", nullable: true, samples: ["1988-03-12", "1975-11-30"] },
  "adt.person.sex_code": { sql_type: "char(1)", nullable: false, samples: ["M", "F", "U"] },
  "adt.encounter.enc_id": { sql_type: "bigint", nullable: false, samples: ["550012", "550013"] },
  "adt.encounter.admit_ts": { sql_type: "timestamp", nullable: false, samples: ["2026-11-03 14:30:00", "2026-11-04 09:00:00"] },
  "adt.encounter.class_code": { sql_type: "varchar(4)", nullable: false, samples: ["IP", "OP", "ER", "OBS"] },
  "adt.encounter.status": { sql_type: "varchar(16)", nullable: false, samples: ["in-progress", "finished"] },
  "lis.result.loinc_code": { sql_type: "varchar(10)", nullable: false, samples: ["2160-0", "718-7"] },
  "lis.result.value_num": { sql_type: "numeric(12,3)", nullable: true, samples: ["0.95", "13.4"] },
  "lis.result.unit": { sql_type: "varchar(20)", nullable: true, samples: ["mg/dL", "g/dL"] },
  "lis.result.status": { sql_type: "varchar(16)", nullable: false, samples: ["final", "preliminary"] }
};

window.SAMPLE_STRUCTURE_MAP = {
  generated_at: "2026-11-06",
  maps: [
    {
      entity: "Patient",
      target_profile: "patient-bupa",
      mappings: [
        {
          source: { table: "adt.person", column: "mrn" },
          target: { profile: "patient-bupa", element: "Patient.identifier", type: "Identifier" },
          transform: "direct", required: true, binding: null,
          confidence: 0.92,
          rationale: "Primary medical record number; unique, not null. Maps to the patient business identifier.",
          citation: { source: "campos_perfiles.csv", ref: "patient-bupa / Patient.identifier" }
        },
        {
          source: { table: "adt.person", column: "family_name" },
          target: { profile: "patient-bupa", element: "Patient.name.family", type: "string" },
          transform: "direct", required: true, binding: null,
          confidence: 0.95,
          rationale: "Family name, not null; element name and type align.",
          citation: { source: "campos_perfiles.csv", ref: "patient-bupa / Patient.name.family" }
        },
        {
          source: { table: "adt.person", column: "birth_date" },
          target: { profile: "patient-bupa", element: "Patient.birthDate", type: "date" },
          transform: "direct", required: false, binding: null,
          confidence: 0.94,
          rationale: "DATE column; direct map to birthDate.",
          citation: { source: "campos_perfiles.csv", ref: "patient-bupa / Patient.birthDate" }
        },
        {
          source: { table: "adt.person", column: "sex_code", via_lookup: "adt.code_value" },
          target: { profile: "patient-bupa", element: "Patient.gender", type: "code" },
          transform: "code-translation", required: true,
          binding: { valueset: "administrative-gender", strength: "required" },
          terminology_notes: "Source codes M/F/U/O; 'U' has no direct administrative-gender code — needs a decision (unknown vs other).",
          confidence: 0.78,
          rationale: "Lookup adt.code_value provides sex labels; target binding is required.",
          citation: { source: "campos_perfiles.csv", ref: "patient-bupa / Patient.gender" }
        }
      ],
      references: [
        {
          source_fk: "adt.person.primary_provider_id -> adt.provider.provider_id",
          target: { element: "Patient.generalPractitioner", type: "Reference", target_profile: "practitioner-bupa" },
          confidence: 0.9,
          rationale: "Declared FK to provider; generalPractitioner accepts practitioner-bupa.",
          citation: { source: "campos_perfiles.csv", ref: "patient-bupa / Patient.generalPractitioner" }
        }
      ],
      unfilled_required: [
        { element: "Patient.identifier.system", reason: "no source column carries the identifier namespace", action: "integration must supply a fixed system URI" }
      ],
      unmapped_source: [
        { table: "adt.person", column: "legacy_flag", reason: "no plausible FHIR destination; confirm with provider" }
      ],
      ambiguities: [
        { location: "adt.person.race_code", question: "map to a US-core-style extension, or drop for Bupa Core?", impact: "guide has no race element; needs product decision" }
      ]
    },
    {
      entity: "Encounter",
      target_profile: "encounter-bupa",
      mappings: [
        {
          source: { table: "adt.encounter", column: "enc_id" },
          target: { profile: "encounter-bupa", element: "Encounter.identifier", type: "Identifier" },
          transform: "direct", required: true, binding: null,
          confidence: 0.91,
          rationale: "Encounter business identifier; unique, not null.",
          citation: { source: "campos_perfiles.csv", ref: "encounter-bupa / Encounter.identifier" }
        },
        {
          source: { table: "adt.encounter", column: "admit_ts" },
          target: { profile: "encounter-bupa", element: "Encounter.period.start", type: "dateTime" },
          transform: "direct", required: true, binding: null,
          confidence: 0.93,
          rationale: "Admission timestamp; direct map to period.start.",
          citation: { source: "campos_perfiles.csv", ref: "encounter-bupa / Encounter.period.start" }
        },
        {
          source: { table: "adt.encounter", column: "class_code", via_lookup: "adt.code_value" },
          target: { profile: "encounter-bupa", element: "Encounter.class", type: "Coding" },
          transform: "code-translation", required: true,
          binding: { valueset: "v3-ActEncounterCode", strength: "extensible" },
          terminology_notes: "Local classes (IP/OP/ER/OBS) need translation to v3 ActEncounterCode; 'OBS' maps to observation encounter.",
          confidence: 0.72,
          rationale: "Lookup provides class labels; extensible binding allows local additions.",
          citation: { source: "campos_perfiles.csv", ref: "encounter-bupa / Encounter.class" }
        },
        {
          source: { table: "adt.encounter", column: "status" },
          target: { profile: "encounter-bupa", element: "Encounter.status", type: "code" },
          transform: "code-translation", required: true,
          binding: { valueset: "encounter-status", strength: "required" },
          confidence: 0.84,
          rationale: "Status labels align closely with FHIR encounter-status.",
          citation: { source: "campos_perfiles.csv", ref: "encounter-bupa / Encounter.status" }
        }
      ],
      references: [
        {
          source_fk: "adt.encounter.person_id -> adt.person.person_id",
          target: { element: "Encounter.subject", type: "Reference", target_profile: "patient-bupa" },
          confidence: 0.95,
          rationale: "Declared FK to person; subject accepts patient-bupa.",
          citation: { source: "campos_perfiles.csv", ref: "encounter-bupa / Encounter.subject" }
        }
      ],
      unfilled_required: [
        { element: "Encounter.type", reason: "no source column found for encounter type", action: "derive from class, or supply a fixed value per feed" }
      ],
      unmapped_source: [],
      ambiguities: [
        { location: "adt.encounter.discharge_disposition", question: "hospitalization.dischargeDisposition or a custom extension?", impact: "affects downstream discharge reporting" }
      ]
    },
    {
      entity: "Observation",
      target_profile: "observation-bupa",
      mappings: [
        {
          source: { table: "lis.result", column: "loinc_code" },
          target: { profile: "observation-bupa", element: "Observation.code", type: "CodeableConcept" },
          transform: "direct", required: true,
          binding: { valueset: "observation-codes", strength: "preferred" },
          confidence: 0.9,
          rationale: "Values match LOINC pattern; direct map to Observation.code.",
          citation: { source: "campos_perfiles.csv", ref: "observation-bupa / Observation.code" }
        },
        {
          source: { table: "lis.result", column: "value_num" },
          target: { profile: "observation-bupa", element: "Observation.valueQuantity.value", type: "decimal" },
          transform: "direct", required: false, binding: null,
          confidence: 0.86,
          rationale: "Numeric result value; maps to valueQuantity.value.",
          citation: { source: "campos_perfiles.csv", ref: "observation-bupa / Observation.valueQuantity" }
        },
        {
          source: { table: "lis.result", column: "unit" },
          target: { profile: "observation-bupa", element: "Observation.valueQuantity.unit", type: "string" },
          transform: "direct", required: false,
          binding: { valueset: "ucum-units", strength: "preferred" },
          terminology_notes: "Local units (mg/dL, mmol/L) should be validated against UCUM.",
          confidence: 0.69,
          rationale: "Unit string present; UCUM alignment not verified — routed for review.",
          citation: { source: "campos_perfiles.csv", ref: "observation-bupa / Observation.valueQuantity" }
        },
        {
          source: { table: "lis.result", column: "status" },
          target: { profile: "observation-bupa", element: "Observation.status", type: "code" },
          transform: "code-translation", required: true,
          binding: { valueset: "observation-status", strength: "required" },
          confidence: 0.82,
          rationale: "Status labels map to FHIR observation-status.",
          citation: { source: "campos_perfiles.csv", ref: "observation-bupa / Observation.status" }
        }
      ],
      references: [
        {
          source_fk: "lis.result.person_id -> adt.person.person_id",
          target: { element: "Observation.subject", type: "Reference", target_profile: "patient-bupa" },
          confidence: 0.94,
          rationale: "Declared FK to person; subject accepts patient-bupa.",
          citation: { source: "campos_perfiles.csv", ref: "observation-bupa / Observation.subject" }
        }
      ],
      unfilled_required: [
        { element: "Observation.category", reason: "no source column classifies the observation (e.g. laboratory)", action: "supply fixed 'laboratory' for LIS results" }
      ],
      unmapped_source: [
        { table: "lis.result", column: "legacy_lis_code", reason: "superseded by loinc_code; retain only for traceability" }
      ],
      ambiguities: [
        { location: "lis.result.abnormal_flag", question: "map to Observation.interpretation with which value set?", impact: "flag codes (H/L/HH/LL) need a terminology decision" }
      ]
    }
  ]
};
