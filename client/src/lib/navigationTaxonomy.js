/**
 * Government branch navigation: maps UI paths to API filters
 * (SubjectProfile.branch, government_level, subject_type).
 */

export const TOP_LEVELS = [
  { id: "federal", label: "Federal" },
  { id: "state", label: "State" },
  { id: "local", label: "Local" },
];

/** Default home filter: Federal legislature → Senate. */
export const DEFAULT_FEDERAL_SENATE_NAV = {
  branch: "legislative",
  government_level: "federal",
  subject_type: "senator",
};

/** Nested structure for sidebar / subnav */
export const GOVERNMENT_NAV_TREE = [
  {
    levelId: "federal",
    label: "Federal",
    branches: [
      {
        branchId: "legislative",
        label: "Legislative",
        items: [
          {
            label: "Senate",
            branch: "legislative",
            government_level: "federal",
            subject_type: "senator",
          },
          {
            label: "House",
            branch: "legislative",
            government_level: "federal",
            subject_type: "house_member",
          },
        ],
      },
      {
        branchId: "executive",
        label: "Executive",
        items: [
          {
            label: "President",
            branch: "executive",
            government_level: "federal",
            subject_type: "executive",
          },
          {
            label: "Vice President",
            branch: "executive",
            government_level: "federal",
            subject_type: "vp",
          },
        ],
      },
      {
        branchId: "judicial",
        label: "Judicial",
        items: [
          {
            label: "Supreme Court",
            branch: "judicial",
            government_level: "federal",
            subject_type: "federal_judge_scotus",
          },
          {
            label: "Circuit Courts",
            branch: "judicial",
            government_level: "federal",
            subject_type: "federal_judge_circuit",
          },
          {
            label: "District Courts",
            branch: "judicial",
            government_level: "federal",
            subject_type: "federal_judge_district",
          },
          {
            label: "Magistrate Courts",
            branch: "judicial",
            government_level: "federal",
            subject_type: "federal_judge_magistrate",
          },
          {
            label: "Bankruptcy Courts",
            branch: "judicial",
            government_level: "federal",
            subject_type: "federal_judge_bankruptcy",
          },
        ],
      },
    ],
  },
  {
    levelId: "state",
    label: "State",
    branches: [
      {
        branchId: "state_executive",
        label: "Executive & law",
        items: [
          {
            label: "Governors",
            branch: "executive",
            government_level: "state",
            subject_type: "state_governor",
          },
          {
            label: "Attorneys General",
            branch: "executive",
            government_level: "state",
            subject_type: "state_attorney_general",
          },
        ],
      },
      {
        branchId: "state_legislative",
        label: "Legislative",
        items: [
          {
            label: "Legislators",
            branch: "legislative",
            government_level: "state",
            subject_type: "state_legislator",
          },
        ],
      },
      {
        branchId: "state_judicial",
        label: "Judicial",
        items: [
          {
            label: "State Judges",
            branch: "judicial",
            government_level: "state",
            subject_type: "state_judge",
          },
        ],
      },
      {
        branchId: "state_other",
        label: "Other",
        items: [
          {
            label: "Other state officials",
            branch: "administrative",
            government_level: "state",
            subject_type: "public_official",
          },
        ],
      },
    ],
  },
  {
    levelId: "local",
    label: "Local",
    branches: [
      {
        branchId: "local_government",
        label: "Government",
        items: [
          {
            label: "Mayors",
            branch: "executive",
            government_level: "local",
            subject_type: "mayor",
          },
          {
            label: "City Council",
            branch: "legislative",
            government_level: "local",
            subject_type: "city_council",
          },
        ],
      },
      {
        branchId: "local_law",
        label: "Law enforcement & prosecution",
        items: [
          {
            label: "District Attorneys",
            branch: "executive",
            government_level: "local",
            subject_type: "district_attorney",
          },
          {
            label: "Sheriffs",
            branch: "executive",
            government_level: "local",
            subject_type: "sheriff",
          },
          {
            label: "Prosecutors",
            branch: "executive",
            government_level: "local",
            subject_type: "county_prosecutor",
          },
        ],
      },
      {
        branchId: "local_education",
        label: "Education",
        items: [
          {
            label: "School Boards",
            branch: "administrative",
            government_level: "local",
            subject_type: "school_board_member",
          },
        ],
      },
      {
        branchId: "local_appointed",
        label: "Appointed officials",
        items: [
          {
            label: "Zoning & Planning",
            branch: "administrative",
            government_level: "local",
            subject_type: "zoning_board_member",
          },
          {
            label: "Utilities & Transit",
            branch: "administrative",
            government_level: "local",
            subject_type: "utility_board_member",
          },
          {
            label: "Inspectors General",
            branch: "administrative",
            government_level: "local",
            subject_type: "inspector_general",
          },
          {
            label: "Other boards",
            branch: "administrative",
            government_level: "local",
            subject_type: "special_district_board_member",
          },
        ],
      },
    ],
  },
];

/** Flatten nav leaves with stable ids for active highlighting */
export function flattenNavLeaves() {
  const out = [];
  for (const top of GOVERNMENT_NAV_TREE) {
    for (const br of top.branches) {
      for (const item of br.items) {
        out.push({
          ...item,
          topLevelId: top.levelId,
          branchGroupLabel: br.label,
        });
      }
    }
  }
  return out;
}

export function navMatchesSelection(item, branch, level, type) {
  if (!branch && !level && !type) return false;
  return (
    (!branch || item.branch === branch) &&
    (!level || item.government_level === level) &&
    (!type || item.subject_type === type)
  );
}
