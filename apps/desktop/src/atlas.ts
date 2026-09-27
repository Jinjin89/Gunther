export type AtlasMode = "overview" | "sources" | "ask" | "outputs";
export type AtlasZoom = "field" | "chapter" | "topic";

export interface KnowledgeSource {
  id: string;
  title: string;
  publisher: string;
  kind: "guide" | "documentation" | "ontology" | "dataset";
  url: string;
  scope: string;
  usedIn: string[];
}

export interface KnowledgeTopic {
  id: string;
  title: string;
  summary: string;
  markers?: string[];
  caution?: string;
}

export interface KnowledgeChapter {
  id: string;
  number: string;
  title: string;
  question: string;
  summary: string;
  status: "grounded" | "growing" | "outline";
  progress: number;
  sourceIds: string[];
  takeaways: string[];
  decision: {
    label: string;
    answer: string;
  };
  topics: KnowledgeTopic[];
}

export interface AtlasPopulation {
  id: string;
  label: string;
  family: string;
  chapterId: string;
  x: number;
  y: number;
  confidence: number;
  markers: string[];
  note: string;
}

export interface KnowledgeBase {
  id: string;
  eyebrow: string;
  title: string;
  subtitle: string;
  question: string;
  description: string;
  color: "green" | "blue" | "clay";
  status: "Living" | "Growing" | "Outline";
  progress: number;
  updated: string;
  chapterCount: number;
  sourceCount: number;
  indexedSourceCount?: number;
  chapters: KnowledgeChapter[];
  sources: KnowledgeSource[];
  populations?: AtlasPopulation[];
}

export interface InboxChange {
  id: string;
  baseId: string;
  label: string;
  title: string;
  summary: string;
  impact: string[];
  source: string;
  status: "pending" | "accepted" | "held";
  retryCaptureId?: string;
  sourceId?: string;
  claimCount?: number;
}

const singleCellSources: KnowledgeSource[] = [
  {
    id: "best-practices-annotation",
    title: "Single-cell Best Practices · Annotation",
    publisher: "Theis Lab / scverse community",
    kind: "guide",
    url: "https://www.sc-best-practices.org/cellular_structure/annotation.html",
    scope: "Manual markers, automated annotation, reference transfer, and uncertainty.",
    usedIn: ["identity", "validation"],
  },
  {
    id: "best-practices-qc",
    title: "Single-cell Best Practices · Quality control",
    publisher: "Theis Lab / scverse community",
    kind: "guide",
    url: "https://www.sc-best-practices.org/preprocessing_visualization/quality_control.html",
    scope: "Empty droplets, low-quality cells, doublets, and iterative filtering.",
    usedIn: ["context", "qc"],
  },
  {
    id: "scanpy-clustering",
    title: "Scanpy clustering tutorial",
    publisher: "scverse",
    kind: "documentation",
    url: "https://scanpy.readthedocs.io/en/latest/tutorials/basics/clustering.html",
    scope: "Feature selection, representation, clustering, markers, and broad annotation.",
    usedIn: ["representation", "structure", "identity"],
  },
  {
    id: "scanpy-pbmc",
    title: "PBMC 3k processed dataset",
    publisher: "Scanpy / 10x Genomics",
    kind: "dataset",
    url: "https://scanpy.readthedocs.io/en/stable/api/scanpy.datasets.pbmc3k_processed.html",
    scope: "The concrete 2,638-cell demonstration workbook used in this prototype.",
    usedIn: ["context", "representation", "structure", "identity", "validation"],
  },
  {
    id: "seurat-mapping",
    title: "Multimodal reference mapping",
    publisher: "Satija Lab",
    kind: "documentation",
    url: "https://satijalab.org/seurat/articles/multimodal_reference_mapping",
    scope: "Reference label transfer, prediction scores, projection, and marker validation.",
    usedIn: ["reference", "validation"],
  },
  {
    id: "celltypist",
    title: "CellTypist tutorial",
    publisher: "CellTypist",
    kind: "documentation",
    url: "https://celltypist.readthedocs.io/en/latest/notebook/celltypist_tutorial.html",
    scope: "Per-cell classification, confidence scores, and majority voting.",
    usedIn: ["reference", "validation"],
  },
  {
    id: "cell-ontology",
    title: "Cell Ontology",
    publisher: "EMBL-EBI OLS",
    kind: "ontology",
    url: "https://www.ebi.ac.uk/ols4/ontologies/cl",
    scope: "Stable cell-type identifiers and parent–child terminology.",
    usedIn: ["identity", "publish"],
  },
];

const singleCellChapters: KnowledgeChapter[] = [
  {
    id: "context",
    number: "01",
    title: "Frame the biological question",
    question: "What can this experiment actually resolve?",
    summary: "Annotation begins with tissue, condition, assay, sampling, and the biological question—not with a marker list.",
    status: "grounded",
    progress: 100,
    sourceIds: ["best-practices-qc", "scanpy-pbmc"],
    takeaways: [
      "Record organism, tissue, condition, assay, and sampling before interpreting clusters.",
      "Expected cell populations are a prior to test, not a list to force onto the data.",
      "PBMC 3k is a teaching dataset; its label space should not be generalized to every tissue.",
    ],
    decision: { label: "Workbook scope", answer: "Peripheral blood · healthy donor · 10x 3′ gene expression" },
    topics: [
      { id: "study-context", title: "Study context", summary: "Capture biological and technical boundaries before analysis." },
      { id: "expected-space", title: "Expected label space", summary: "State plausible lineages while leaving room for unknown states." },
    ],
  },
  {
    id: "qc",
    number: "02",
    title: "Decide what counts as a cell",
    question: "Which barcodes carry trustworthy biological signal?",
    summary: "Separate intact cells from empty droplets, stressed or damaged cells, ambient RNA, and likely multiplets before identities harden.",
    status: "grounded",
    progress: 92,
    sourceIds: ["best-practices-qc"],
    takeaways: [
      "QC thresholds are dataset-dependent; inspect distributions instead of copying fixed cutoffs.",
      "Doublet detection and ambient RNA checks are part of annotation quality.",
      "Revisit QC after preliminary labels reveal suspicious or mixed populations.",
    ],
    decision: { label: "Review gate", answer: "Flag low-complexity, high-mitochondrial, and mixed-lineage profiles" },
    topics: [
      { id: "empty-droplets", title: "Empty droplets", summary: "Distinguish cell-containing barcodes from ambient RNA background." },
      { id: "doublets", title: "Doublets", summary: "Look for incompatible lineage programs and anomalous complexity.", caution: "A mixed program can also reflect biology; keep the reason for every exclusion." },
      { id: "stress", title: "Stress and damage", summary: "Separate technical stress signals from stable biological identity." },
    ],
  },
  {
    id: "representation",
    number: "03",
    title: "Build a faithful representation",
    question: "Which variation should shape the neighborhood graph?",
    summary: "Normalize counts, select informative features, reduce dimensions, and inspect whether technical effects dominate the representation.",
    status: "grounded",
    progress: 86,
    sourceIds: ["scanpy-clustering", "scanpy-pbmc"],
    takeaways: [
      "Keep raw counts available while derived representations evolve.",
      "Highly variable genes and dimensionality choices influence every downstream boundary.",
      "Visual embeddings support exploration; they are not proof of discrete cell types.",
    ],
    decision: { label: "Demo representation", answer: "2,638 cells · 1,838 genes · PCA neighborhood graph" },
    topics: [
      { id: "normalization", title: "Normalization", summary: "Make cells comparable without overwriting the original count layer." },
      { id: "features", title: "Feature selection", summary: "Choose informative variation while monitoring technical programs." },
      { id: "neighbors", title: "Neighborhood graph", summary: "Represent local similarity for clustering and visualization." },
    ],
  },
  {
    id: "structure",
    number: "04",
    title: "Discover candidate populations",
    question: "What structure is stable enough to investigate?",
    summary: "Clustering proposes neighborhoods for interpretation. Resolution is a lens, not a biological fact, and subclustering should follow a question.",
    status: "growing",
    progress: 78,
    sourceIds: ["scanpy-clustering"],
    takeaways: [
      "Compare resolutions and stability before naming a population.",
      "Annotate broad lineages first, then zoom into well-supported substructure.",
      "A cluster is an analytical unit; a cell type is a biological claim.",
    ],
    decision: { label: "Current granularity", answer: "8 broad immune and platelet populations" },
    topics: [
      { id: "resolution", title: "Resolution", summary: "Explore alternate partitions and record why a level was chosen." },
      { id: "subclustering", title: "Purposeful subclustering", summary: "Zoom only where the biological question and signal justify it." },
    ],
  },
  {
    id: "identity",
    number: "05",
    title: "Build an identity case",
    question: "Which converging signals support each label?",
    summary: "Combine coherent positive and negative marker programs, differential expression, neighborhood context, and biological plausibility.",
    status: "grounded",
    progress: 88,
    sourceIds: ["best-practices-annotation", "scanpy-clustering", "cell-ontology"],
    takeaways: [
      "A marker is evidence inside a program—not an identity by itself.",
      "Use negative evidence and incompatible programs to challenge a proposed label.",
      "Choose the narrowest ontology-backed label the evidence can support.",
    ],
    decision: { label: "Naming rule", answer: "Broad lineage first; subtype only with converging evidence" },
    topics: [
      { id: "t-cells", title: "T-cell compartment", summary: "Resolve a shared T-cell program before CD4/CD8 or state-level refinement.", markers: ["CD3D", "CD3E", "TRAC", "IL7R", "CD8A"], caution: "CD3 genes support T-cell identity but do not alone establish a T-cell subtype." },
      { id: "nk-cells", title: "NK compartment", summary: "Use a cytotoxic/NK program and the relative absence of a coherent TCR program.", markers: ["NKG7", "GNLY", "FCER1G", "TYROBP"] },
      { id: "b-cells", title: "B-cell compartment", summary: "Follow the B-cell receptor and antigen-presentation program.", markers: ["MS4A1", "CD79A", "CD37", "HLA-DRA"] },
      { id: "myeloid", title: "Myeloid compartment", summary: "Separate classical and FCGR3A-associated monocyte programs before rare dendritic calls.", markers: ["LYZ", "CTSS", "FCN1", "CD14", "FCGR3A", "MS4A7"] },
      { id: "rare", title: "Rare populations", summary: "Require stronger evidence and rule out doublets or ambient signatures.", markers: ["FCER1A", "CST3", "PPBP", "PF4"], caution: "Small clusters are especially sensitive to QC, resolution, and contamination." },
    ],
  },
  {
    id: "reference",
    number: "06",
    title: "Challenge labels with references",
    question: "Does a trusted reference agree—and where should it not?",
    summary: "Use mapping and classifiers as independent proposals. Their reliability depends on reference quality, biological match, and label resolution.",
    status: "growing",
    progress: 71,
    sourceIds: ["best-practices-annotation", "seurat-mapping", "celltypist"],
    takeaways: [
      "Retain prediction scores and mapping uncertainty alongside every transferred label.",
      "A reference cannot reliably name states it does not represent.",
      "Disagreement between manual evidence and reference labels is a review target, not an error to hide.",
    ],
    decision: { label: "Reference policy", answer: "CellTypist proposal + manual marker-program review" },
    topics: [
      { id: "mapping", title: "Atlas mapping", summary: "Project the query into a curated reference and retain prediction scores." },
      { id: "classifier", title: "Supervised classification", summary: "Use per-cell labels and local majority voting as proposals." },
      { id: "reference-gap", title: "Reference gaps", summary: "Investigate low mapping quality as potential technical mismatch or unseen biology." },
    ],
  },
  {
    id: "validation",
    number: "07",
    title: "Validate, version, and publish",
    question: "Can another scientist understand and challenge every label?",
    summary: "Cross-check methods, expose uncertainty, preserve unknowns, and publish labels with evidence, ontology terms, provenance, and revisions.",
    status: "growing",
    progress: 68,
    sourceIds: ["best-practices-annotation", "seurat-mapping", "celltypist", "cell-ontology", "scanpy-pbmc"],
    takeaways: [
      "Unknown is a valid label when the evidence does not support a precise identity.",
      "Validate automated calls systematically against marker programs and neighborhood structure.",
      "Version the annotation vocabulary, evidence, model, thresholds, and human decisions together.",
    ],
    decision: { label: "Release gate", answer: "No label ships without confidence, evidence, and provenance" },
    topics: [
      { id: "uncertainty", title: "Uncertainty", summary: "Flag low-confidence regions and ambiguous populations for targeted review." },
      { id: "consensus", title: "Method agreement", summary: "Compare manual, classifier, and reference-mapping proposals." },
      { id: "provenance", title: "Provenance", summary: "Record source spans, software/model versions, decisions, and ontology identifiers." },
      { id: "export", title: "Reusable release", summary: "Export a human-readable story and a machine-readable annotation table." },
    ],
  },
];

const populations: AtlasPopulation[] = [
  { id: "cd4-t", label: "CD4 T", family: "T cells", chapterId: "identity", x: 29, y: 31, confidence: 0.93, markers: ["CD3D", "IL7R", "LTB"], note: "Broad T-cell program with an IL7R-associated helper profile." },
  { id: "cd8-t", label: "CD8 T", family: "T cells", chapterId: "identity", x: 40, y: 24, confidence: 0.86, markers: ["CD3D", "CD8A", "LTB"], note: "T-cell identity is strong; CD8 subtype remains slightly less resolved." },
  { id: "nk", label: "NK", family: "Cytotoxic", chapterId: "identity", x: 50, y: 36, confidence: 0.96, markers: ["NKG7", "GNLY", "FCER1G"], note: "Coherent NK/cytotoxic program separated from the T-cell neighborhood." },
  { id: "b", label: "B", family: "B cells", chapterId: "identity", x: 24, y: 61, confidence: 0.97, markers: ["MS4A1", "CD79A", "HLA-DRA"], note: "Strong B-cell receptor and antigen-presentation program." },
  { id: "cd14", label: "CD14 Mono", family: "Myeloid", chapterId: "identity", x: 66, y: 62, confidence: 0.95, markers: ["LYZ", "S100A8", "CD14"], note: "Classical monocyte program is well supported." },
  { id: "fcgr3a", label: "FCGR3A Mono", family: "Myeloid", chapterId: "identity", x: 76, y: 49, confidence: 0.82, markers: ["FCGR3A", "MS4A7", "LYN"], note: "Distinct monocyte program, with a softer boundary toward dendritic-like cells." },
  { id: "dc", label: "Dendritic", family: "Rare", chapterId: "identity", x: 84, y: 27, confidence: 0.67, markers: ["FCER1A", "CST3"], note: "Rare population; review contamination, doublets, and reference agreement." },
  { id: "platelet", label: "Platelet", family: "Platelet", chapterId: "identity", x: 60, y: 16, confidence: 0.91, markers: ["PPBP", "PF4"], note: "Compact platelet-associated program." },
];

const compactChapter = (id: string, number: string, title: string, summary: string): KnowledgeChapter => ({
  id,
  number,
  title,
  question: title,
  summary,
  status: "outline",
  progress: 28,
  sourceIds: [],
  takeaways: ["This chapter is ready to grow from captured sources and your own decisions."],
  decision: { label: "Next step", answer: "Add a trusted source or working note" },
  topics: [],
});

export const knowledgeBases: KnowledgeBase[] = [
  {
    id: "single-cell-annotation",
    eyebrow: "Computational biology",
    title: "Single-cell RNA-seq Annotation",
    subtitle: "From raw barcodes to defensible cell identities",
    question: "How do we turn expression profiles into cell identities we can explain, challenge, and reuse?",
    description: "A living field guide that connects experimental context, data quality, analytical choices, biological evidence, reference atlases, and uncertainty.",
    color: "green",
    status: "Living",
    progress: 83,
    updated: "Today",
    chapterCount: singleCellChapters.length,
    sourceCount: singleCellSources.length,
    chapters: singleCellChapters,
    sources: singleCellSources,
    populations,
  },
  {
    id: "full-stack-development",
    eyebrow: "Software engineering",
    title: "Full-stack Development",
    subtitle: "A system from interface to operations",
    question: "How do the layers of a reliable product fit together?",
    description: "Architecture, frontend, backend, data, delivery, and operations organized as one coherent practice.",
    color: "blue",
    status: "Growing",
    progress: 42,
    updated: "Yesterday",
    chapterCount: 6,
    sourceCount: 12,
    chapters: [
      compactChapter("architecture", "01", "Architecture", "Boundaries, responsibilities, data flow, and trade-offs."),
      compactChapter("frontend", "02", "Frontend", "Interfaces, state, accessibility, performance, and interaction design."),
      compactChapter("backend", "03", "Backend & APIs", "Domain logic, contracts, services, and failure handling."),
      compactChapter("data", "04", "Data & identity", "Persistence, migrations, authorization, and privacy."),
      compactChapter("testing", "05", "Testing & delivery", "Confidence from local feedback to continuous delivery."),
      compactChapter("operations", "06", "Operations", "Observability, security, scale, and incident learning."),
    ],
    sources: [],
  },
  {
    id: "project-management",
    eyebrow: "Leadership",
    title: "Project Management",
    subtitle: "Move uncertain work toward a useful outcome",
    question: "How do we create clarity without pretending uncertainty is gone?",
    description: "A practical knowledge base for framing, planning, coordination, risk, delivery, and organizational learning.",
    color: "clay",
    status: "Outline",
    progress: 18,
    updated: "3 days ago",
    chapterCount: 6,
    sourceCount: 4,
    chapters: [
      compactChapter("frame", "01", "Frame the outcome", "Turn requests into purpose, boundaries, and observable success."),
      compactChapter("plan", "02", "Shape the plan", "Sequence uncertainty, dependencies, decisions, and learning."),
      compactChapter("execute", "03", "Run the work", "Maintain flow, ownership, quality, and fast feedback."),
      compactChapter("communicate", "04", "Create shared context", "Make status, decisions, and risks easy to understand."),
      compactChapter("risk", "05", "Manage uncertainty", "Surface risk early and design proportionate responses."),
      compactChapter("learn", "06", "Close the loop", "Ship, measure, reflect, and update the operating knowledge."),
    ],
    sources: [],
  },
];

export const initialInboxChanges: InboxChange[] = [];

export const getKnowledgeBase = (id: string) => knowledgeBases.find((base) => base.id === id) ?? knowledgeBases[0]!;
