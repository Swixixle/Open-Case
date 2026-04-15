export const PATTERN_ALERT_LABELS = {
  COMMITTEE_SWEEP_V1: {
    display: "Committee donor sweep",
    description:
      "Multiple donors from industries directly regulated by this official's committee contributed in the same cycle",
  },
  FINGERPRINT_BLOOM_V1: {
    display: "Shared donor network",
    description:
      "This official shares an unusual number of donors with another official under investigation",
  },
  SOFT_BUNDLE_V1: {
    display: "Donor cluster near vote",
    description:
      "A cluster of donations from the same sector arrived within days of a related legislative vote",
  },
  SOFT_BUNDLE_V2: {
    display: "Donor cluster near vote",
    description:
      "A cluster of donations from the same sector arrived within days of a related legislative vote",
  },
  SECTOR_CONVERGENCE_V1: {
    display: "Sector concentration",
    description:
      "Donations from a single industry represent an unusually high share of total fundraising during committee oversight of that industry",
  },
  GEO_MISMATCH_V1: {
    display: "Out-of-state donor anomaly",
    description:
      "An unusually high share of donations came from outside the official's home state or district",
  },
  DISBURSEMENT_LOOP_V1: {
    display: "PAC disbursement loop",
    description:
      "PAC funds flow in a pattern suggesting coordination between donor and recipient committees",
  },
  JOINT_FUNDRAISING_V1: {
    display: "Joint fundraising signal",
    description:
      "Joint fundraising committee activity suggests coordinated donor networks",
  },
  BASELINE_ANOMALY_V1: {
    display: "Unusual donation pattern",
    description:
      "Donation timing or amount deviates significantly from this official's own historical baseline",
  },
  ALIGNMENT_ANOMALY_V1: {
    display: "Vote/donor misalignment",
    description:
      "Voting record diverges from stated position in a pattern that correlates with donor activity",
  },
  AMENDMENT_TELL_V1: {
    display: "Amendment timing signal",
    description:
      "Donations arrived in proximity to a specific amendment sponsorship or vote",
  },
  HEARING_TESTIMONY_V1: {
    display: "Testimony donor overlap",
    description:
      "Entities that testified before this official's committee also appear as donors",
  },
  REVOLVING_DOOR_V1: {
    display: "Revolving door",
    description:
      "Former staff moved to lobbying roles representing industries this official oversees",
  },
};

export function patternAlertDisplay(ruleId) {
  const row = PATTERN_ALERT_LABELS[ruleId];
  return row?.display || (ruleId ? ruleId.replace(/_/g, " ").toLowerCase() : "Pattern alert");
}

export function patternAlertDescription(ruleId) {
  return PATTERN_ALERT_LABELS[ruleId]?.description || "";
}
