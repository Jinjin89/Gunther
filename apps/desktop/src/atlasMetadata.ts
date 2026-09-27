import type { KnowledgeBaseMetadata } from "@gunther/contracts";
import { knowledgeBases, type KnowledgeBase, type KnowledgeChapter } from "./atlas";

export const metadataToBase = (metadata: KnowledgeBaseMetadata): KnowledgeBase => {
  const bundled = knowledgeBases.find((base) => base.id === metadata.id);
  if (bundled) return {
    ...bundled,
    title: metadata.title,
    eyebrow: metadata.eyebrow,
    subtitle: metadata.subtitle,
    question: metadata.question,
    description: metadata.description,
    color: metadata.color,
    status: metadata.status,
    indexedSourceCount: metadata.sourceCount,
  };
  const overview: KnowledgeChapter = {
    id: "overview",
    number: "01",
    title: "Overview",
    question: metadata.question,
    summary: "Shape the field, establish its boundaries, and connect the first grounded sources.",
    status: "outline",
    progress: 0,
    sourceIds: [],
    takeaways: ["This knowledge base is ready for its first source, question, and reviewed insight."],
    decision: { label: "Next step", answer: "Add a trusted source or begin a grounded session" },
    topics: [],
  };
  return {
    id: metadata.id,
    eyebrow: metadata.eyebrow,
    title: metadata.title,
    subtitle: metadata.subtitle,
    question: metadata.question,
    description: metadata.description,
    color: metadata.color,
    status: metadata.status,
    progress: metadata.sourceCount ? 12 : 0,
    updated: "Today",
    chapterCount: 1,
    sourceCount: 0,
    indexedSourceCount: metadata.sourceCount,
    chapters: [overview],
    sources: [],
  };
};
