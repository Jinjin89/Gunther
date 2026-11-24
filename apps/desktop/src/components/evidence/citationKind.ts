import type { ConversationCitation } from "@gunther/contracts";

/** Older citations may omit kind; a URL without a library source identifies a web passage. */
export function isWebCitation(citation: ConversationCitation): boolean {
  return citation.kind ? citation.kind === "web" : !citation.sourceId && Boolean(citation.url);
}
