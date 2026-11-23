import { Download, MoreHorizontal, Settings2 } from "lucide-react";
import { PopMenu } from "../contents/SectionMenu";

export function LibraryActions({ onEdit, onExport }: {
  onEdit: () => void;
  onExport: () => void;
}) {
  return <div className="gx-library-navigation" data-no-drag>
    <PopMenu label="Library actions" triggerLabel="Library actions" triggerClass="gx-icon-button" trigger={<MoreHorizontal size={15} />} items={[
      { label: "Edit library details", icon: Settings2, onSelect: onEdit },
      { label: "Export library", icon: Download, onSelect: onExport },
    ]} />
  </div>;
}
