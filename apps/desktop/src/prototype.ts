import type { KnowledgeGraph, Overview } from "@gunther/contracts";

export type DomainId = "all" | "biology" | "mathematics" | "management";
export type UnitStatus = "trusted" | "provisional" | "disputed";

export interface UnitClaim {
  id: string;
  subject: string;
  predicate: string;
  object: string;
  status: UnitStatus;
  confidence: number;
  qualifiers: string[];
  evidenceCount: number;
}

export interface UnitEvidence {
  id: string;
  source: string;
  locator: string;
  quote: string;
  stance: "supports" | "refutes" | "mentions";
}

export interface KnowledgeUnit {
  id: string;
  title: string;
  kind: "concept" | "method" | "model" | "example";
  domains: Exclude<DomainId, "all">[];
  summary: string;
  updated: string;
  status: UnitStatus;
  claims: UnitClaim[];
  evidence: UnitEvidence[];
  related: string[];
  usedIn: string[];
  body: Array<{ heading?: string; text: string }>;
}

export interface KnowledgeDelta {
  id: string;
  type: "new" | "reinforced" | "narrowed" | "contradicted";
  title: string;
  detail: string;
  domain: Exclude<DomainId, "all">;
  source: string;
  time: string;
}

export interface ReviewProposal {
  id: string;
  type: "merge" | "qualifier" | "contradiction";
  title: string;
  explanation: string;
  before: string;
  after: string;
  evidence: string;
  source: string;
  status: "pending" | "accepted" | "disputed";
}

export interface StoryScene {
  id: string;
  number: string;
  title: string;
  purpose: string;
  binding: "live" | "pinned";
  unitIds: string[];
  layout: "title" | "evidence" | "comparison";
}

export const domains: Array<{ id: DomainId; label: string; detail: string; count: number }> = [
  { id: "all", label: "All knowledge", detail: "One connected library", count: 24 },
  { id: "biology", label: "Biology", detail: "Cells, genes, evidence", count: 9 },
  { id: "mathematics", label: "Mathematics", detail: "Concepts and proofs", count: 8 },
  { id: "management", label: "Management", detail: "Methods and decisions", count: 7 },
];

export const prototypeUnits: KnowledgeUnit[] = [
  {
    id: "unit-cd3d",
    title: "CD3D as a T-cell marker",
    kind: "concept",
    domains: ["biology"],
    summary: "CD3D encodes part of the T-cell receptor complex and is a strong identity marker for mature T cells in human PBMC single-cell data.",
    updated: "12 min ago",
    status: "trusted",
    claims: [
      { id: "claim-1", subject: "CD3D", predicate: "marker of", object: "T cell", status: "trusted", confidence: 0.96, qualifiers: ["human", "PBMC", "scRNA-seq"], evidenceCount: 3 },
      { id: "claim-2", subject: "CD3D", predicate: "part of", object: "TCR-CD3 complex", status: "trusted", confidence: 0.98, qualifiers: ["cell surface"], evidenceCount: 2 },
      { id: "claim-3", subject: "CD3D low", predicate: "may indicate", object: "ambient RNA or low quality", status: "provisional", confidence: 0.72, qualifiers: ["PBMC"], evidenceCount: 1 },
    ],
    evidence: [
      { id: "ev-1", source: "PBMC annotation field guide", locator: "p. 14 · paragraph 2", quote: "CD3D and CD3E identify the major T-cell compartment, with IL7R and LST1 separating major immune branches.", stance: "supports" },
      { id: "ev-2", source: "Cell annotation workshop", locator: "00:18:42–00:19:18", quote: "Do not call a T cell from one marker alone; look for coherent CD3D, CD3E and TRBC expression.", stance: "supports" },
    ],
    related: ["unit-tcell", "unit-pbmc", "unit-backprop"],
    usedIn: ["story-tcell-identity", "canvas-pbmc"],
    body: [
      { text: "CD3D is useful because it connects a measurable transcript to a stable biological complex: the T-cell receptor–CD3 complex." },
      { heading: "How to use it", text: "Treat CD3D as strong evidence, not a single-marker verdict. Confirm it with CD3E, TRBC1/2, and the absence of lineage-conflicting programs." },
      { heading: "Boundary conditions", text: "Low counts, ambient RNA, doublets, and activated states can weaken or blur the signal. Tissue and assay context belong on the claim." },
    ],
  },
  {
    id: "unit-tcell",
    title: "T-cell identity in PBMC data",
    kind: "model",
    domains: ["biology"],
    summary: "A robust T-cell annotation combines receptor-complex genes, lineage programs, negative evidence, and dataset context.",
    updated: "Today",
    status: "provisional",
    claims: [
      { id: "claim-4", subject: "T cell", predicate: "expresses", object: "CD3D and CD3E", status: "trusted", confidence: 0.95, qualifiers: ["human", "PBMC"], evidenceCount: 3 },
      { id: "claim-5", subject: "Naive CD4 T cell", predicate: "expresses", object: "IL7R and CCR7", status: "provisional", confidence: 0.84, qualifiers: ["human"], evidenceCount: 2 },
    ],
    evidence: [{ id: "ev-3", source: "PBMC annotation field guide", locator: "p. 15 · table 3", quote: "Use a marker program and exclusion markers together when assigning immune cell identity.", stance: "supports" }],
    related: ["unit-cd3d", "unit-pbmc"],
    usedIn: ["story-tcell-identity"],
    body: [
      { text: "Cell identity is an inference from a program, not a lookup from one gene. Gunther keeps each marker relationship qualified and cited." },
      { heading: "Decision pattern", text: "First identify the T-cell compartment, then distinguish major subtypes, then inspect activation and quality effects." },
    ],
  },
  {
    id: "unit-pbmc",
    title: "PBMC annotation workflow",
    kind: "method",
    domains: ["biology"],
    summary: "A repeatable workflow for assigning immune-cell labels while preserving evidence, uncertainty, and revision history.",
    updated: "Yesterday",
    status: "trusted",
    claims: [{ id: "claim-6", subject: "PBMC annotation", predicate: "requires", object: "contextual marker programs", status: "trusted", confidence: 0.94, qualifiers: ["scRNA-seq"], evidenceCount: 4 }],
    evidence: [{ id: "ev-4", source: "PBMC annotation field guide", locator: "p. 4", quote: "Annotation should proceed from broad compartments to fine subtypes with dataset-aware checks at each stage.", stance: "supports" }],
    related: ["unit-cd3d", "unit-tcell"],
    usedIn: ["canvas-pbmc"],
    body: [{ text: "Begin with broad lineage programs, validate with negative evidence, then refine into subtypes and states." }],
  },
  {
    id: "unit-chain-rule",
    title: "Chain rule",
    kind: "concept",
    domains: ["mathematics"],
    summary: "The derivative of a composition is the product of derivatives along the dependency path.",
    updated: "2 days ago",
    status: "trusted",
    claims: [{ id: "claim-7", subject: "Chain rule", predicate: "differentiates", object: "function composition", status: "trusted", confidence: 0.99, qualifiers: ["differentiable functions"], evidenceCount: 3 }],
    evidence: [{ id: "ev-5", source: "Calculus lecture 08", locator: "00:11:04–00:13:20", quote: "Differentiate the outer function, keep the inner expression, then multiply by the derivative of the inner function.", stance: "supports" }],
    related: ["unit-backprop"],
    usedIn: ["story-backprop"],
    body: [{ text: "For y = f(g(x)), dy/dx = f′(g(x))g′(x). The rule is both a formula and a compositional reasoning pattern." }],
  },
  {
    id: "unit-backprop",
    title: "Backpropagation",
    kind: "method",
    domains: ["mathematics"],
    summary: "Backpropagation efficiently applies the chain rule through a computational graph in reverse dependency order.",
    updated: "2 days ago",
    status: "trusted",
    claims: [{ id: "claim-8", subject: "Backpropagation", predicate: "depends on", object: "Chain rule", status: "trusted", confidence: 0.98, qualifiers: ["differentiable graph"], evidenceCount: 4 }],
    evidence: [{ id: "ev-6", source: "Deep learning course · week 2", locator: "00:34:10–00:35:02", quote: "Reverse-mode automatic differentiation reuses intermediate gradients instead of recomputing every derivative path.", stance: "supports" }],
    related: ["unit-chain-rule"],
    usedIn: ["story-backprop"],
    body: [{ text: "The method separates the forward computation from a reverse pass that accumulates local derivatives." }],
  },
  {
    id: "unit-swot",
    title: "SWOT analysis",
    kind: "method",
    domains: ["management"],
    summary: "A framing method that separates internal strengths and weaknesses from external opportunities and threats.",
    updated: "Last week",
    status: "trusted",
    claims: [{ id: "claim-9", subject: "SWOT", predicate: "distinguishes", object: "internal and external factors", status: "trusted", confidence: 0.93, qualifiers: ["strategic planning"], evidenceCount: 2 }],
    evidence: [{ id: "ev-7", source: "Strategy foundations", locator: "chapter 3", quote: "The value of SWOT is in converting observations into testable strategic choices, not filling four boxes.", stance: "supports" }],
    related: ["unit-network-effects"],
    usedIn: ["story-strategy"],
    body: [{ text: "SWOT is most useful when each item is evidenced, scoped, prioritized, and connected to an explicit decision." }],
  },
  {
    id: "unit-network-effects",
    title: "Network effects",
    kind: "concept",
    domains: ["management", "biology"],
    summary: "The value or behavior of a system can change as the number and pattern of connected participants grows.",
    updated: "Last week",
    status: "provisional",
    claims: [{ id: "claim-10", subject: "Network effects", predicate: "depend on", object: "interaction topology", status: "provisional", confidence: 0.81, qualifiers: ["platforms", "ecological systems"], evidenceCount: 2 }],
    evidence: [{ id: "ev-8", source: "Platform strategy notes", locator: "paragraph 18", quote: "Connection density and participant complementarity matter more than user count alone.", stance: "supports" }],
    related: ["unit-swot", "unit-tcell"],
    usedIn: [],
    body: [{ text: "This cross-domain unit distinguishes a shared structural idea from domain-specific mechanisms." }],
  },
];

export const prototypeDeltas: KnowledgeDelta[] = [
  { id: "delta-1", type: "reinforced", title: "CD3D marker claim reinforced", detail: "A workshop transcript adds independent evidence and a useful boundary condition.", domain: "biology", source: "Cell annotation workshop", time: "12 min" },
  { id: "delta-2", type: "narrowed", title: "T-cell identity was narrowed", detail: "The broad marker claim now carries human, PBMC, and scRNA-seq qualifiers.", domain: "biology", source: "PBMC annotation field guide", time: "34 min" },
  { id: "delta-3", type: "contradicted", title: "Single-marker annotation challenged", detail: "One source recommends CD3D alone; two sources require a coherent marker program.", domain: "biology", source: "Three compared sources", time: "1 hr" },
  { id: "delta-4", type: "new", title: "Gradient accumulation added", detail: "A new example connects reverse-mode differentiation to shared intermediate values.", domain: "mathematics", source: "Deep learning course", time: "2 days" },
];

export const prototypeReview: ReviewProposal[] = [
  { id: "review-1", type: "qualifier", title: "Narrow the CD3D marker claim", explanation: "The evidence supports this in human PBMC single-cell data, not as a universal biological statement.", before: "CD3D → marker of → T cell", after: "CD3D → marker of → T cell · human · PBMC · scRNA-seq", evidence: "Three supporting fragments agree on the dataset context.", source: "PBMC annotation field guide", status: "pending" },
  { id: "review-2", type: "contradiction", title: "Keep conflicting annotation advice", explanation: "A course slide recommends a single marker while the field guide requires a coherent program. Preserve both and mark the first as disputed.", before: "CD3D alone is sufficient", after: "CD3D is strong evidence; confirm with a marker program", evidence: "Two independent sources support the revised statement.", source: "Cell annotation workshop", status: "pending" },
  { id: "review-3", type: "merge", title: "Merge “T lymphocyte” into “T cell”", explanation: "The labels resolve to one canonical entity in this library and share every current relation.", before: "T lymphocyte · 2 claims", after: "T cell · alias: T lymphocyte · 8 claims", evidence: "Alias match plus identical ontology identifier.", source: "Entity resolver", status: "pending" },
];

export const prototypeScenes: StoryScene[] = [
  { id: "scene-1", number: "01", title: "What makes a T cell a T cell?", purpose: "Open with the central question", binding: "live", unitIds: ["unit-tcell"], layout: "title" },
  { id: "scene-2", number: "02", title: "CD3D is strong evidence—not a verdict", purpose: "Explain the evidence and boundary", binding: "live", unitIds: ["unit-cd3d"], layout: "evidence" },
  { id: "scene-3", number: "03", title: "Use a coherent marker program", purpose: "Compare the decision signals", binding: "pinned", unitIds: ["unit-cd3d", "unit-tcell", "unit-pbmc"], layout: "comparison" },
];

export const prototypeGraph: KnowledgeGraph = {
  nodes: [
    { id: "unit-cd3d", label: "CD3D", type: "Gene", assertionCount: 3 },
    { id: "unit-tcell", label: "T cell", type: "CellType", assertionCount: 6 },
    { id: "unit-pbmc", label: "PBMC workflow", type: "LearningUnit", assertionCount: 4 },
    { id: "unit-chain-rule", label: "Chain rule", type: "Concept", assertionCount: 3 },
    { id: "unit-backprop", label: "Backpropagation", type: "LearningUnit", assertionCount: 4 },
    { id: "unit-swot", label: "SWOT", type: "Concept", assertionCount: 2 },
    { id: "unit-network-effects", label: "Network effects", type: "Concept", assertionCount: 2 },
  ],
  edges: [
    { id: "edge-1", source: "unit-cd3d", target: "unit-tcell", label: "marker_of", status: "verified", confidence: 0.96 },
    { id: "edge-2", source: "unit-pbmc", target: "unit-tcell", label: "identifies", status: "verified", confidence: 0.92 },
    { id: "edge-3", source: "unit-backprop", target: "unit-chain-rule", label: "depends_on", status: "verified", confidence: 0.98 },
    { id: "edge-4", source: "unit-network-effects", target: "unit-swot", label: "informs", status: "provisional", confidence: 0.74 },
    { id: "edge-5", source: "unit-network-effects", target: "unit-tcell", label: "analogous_structure", status: "provisional", confidence: 0.68 },
  ],
};

export const prototypeOverview: Overview = {
  counts: { sources: 12, entities: 37, assertions: 64, provisional: 3 },
  recentSources: [
    { id: "source-1", title: "Cell annotation workshop", kind: "recording", createdAt: new Date().toISOString(), assertionCount: 6, entityCount: 9 },
    { id: "source-2", title: "PBMC annotation field guide", kind: "paper", createdAt: new Date(Date.now() - 86_400_000).toISOString(), assertionCount: 11, entityCount: 14 },
    { id: "source-3", title: "Deep learning course · week 2", kind: "course", createdAt: new Date(Date.now() - 172_800_000).toISOString(), assertionCount: 8, entityCount: 10 },
  ],
  recentAssertions: [],
};

export function unitsForDomain(domain: DomainId): KnowledgeUnit[] {
  return domain === "all" ? prototypeUnits : prototypeUnits.filter((unit) => unit.domains.includes(domain));
}

export function graphForDomain(domain: DomainId): KnowledgeGraph {
  if (domain === "all") return prototypeGraph;
  const unitIds = new Set(unitsForDomain(domain).map((unit) => unit.id));
  return {
    nodes: prototypeGraph.nodes.filter((node) => unitIds.has(node.id)),
    edges: prototypeGraph.edges.filter((edge) => unitIds.has(edge.source) && unitIds.has(edge.target)),
  };
}
