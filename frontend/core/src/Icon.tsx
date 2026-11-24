import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import {
  faArrowTrendUp, faBoxArchive, faBraille, faBuilding, faChartArea, faChartLine, faChartPie, faChartSimple,
  faCheck, faChevronDown, faChevronRight, faEllipsis, faFolder, faFolderOpen, faPen, faRotateLeft, faStar,
  faTable, faTableCellsLarge, faTrashCan, faXmark,
} from "@fortawesome/free-solid-svg-icons";
import { faStar as faStarOutline } from "@fortawesome/free-regular-svg-icons";
import type { IconDefinition } from "@fortawesome/fontawesome-svg-core";

/**
 * Font Awesome Free (solid + regular) icons, imported one by one so unused icons are tree-shaken.
 * Icons: CC BY 4.0, code: MIT. The attribution comments embedded in the package files satisfy the
 * license; don't strip them. Not used inside @rdb/shared (that package stays icon-library free).
 */
const ICONS = {
  client: faBuilding,
  project: faFolder,
  "project-open": faFolderOpen,
  view: faChartLine,
  favorite: faStarOutline,
  "favorite-on": faStar,
  archive: faBoxArchive,
  restore: faRotateLeft,
  delete: faTrashCan,
  "chevron-right": faChevronRight,
  "chevron-down": faChevronDown,
  overflow: faEllipsis,
  close: faXmark,
  edit: faPen,
  check: faCheck,
  // chart / element kinds (portal cards)
  "kind-bar": faChartSimple,
  "kind-line": faChartLine,
  "kind-area": faChartArea,
  "kind-pie": faChartPie,
  "kind-scatter": faBraille,
  "kind-table": faTable,
  "kind-kpi": faArrowTrendUp,
  "kind-dashboard": faTableCellsLarge,
} satisfies Record<string, IconDefinition>;

export type IconName = keyof typeof ICONS;

/** Decorative by default (aria-hidden); pass `title` when the icon carries meaning on its own. */
export function Icon({ name, size = 16, title }: { name: IconName; size?: number; title?: string }) {
  return (
    <FontAwesomeIcon
      icon={ICONS[name]}
      fixedWidth
      title={title}
      aria-hidden={title ? undefined : true}
      style={{ fontSize: size, width: "1.25em", flexShrink: 0 }}
    />
  );
}
