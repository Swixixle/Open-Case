/**
 * Static directory when the cases API is empty or for mixed subject demos.
 * Senators include bioguide_id for dossier routes; other types may omit until a case exists.
 */

export const DIRECTORY_OFFICIALS = [
  { name: "Mitch McConnell", bioguide_id: "M000355", state: "KY", party: "R", subject_type: "senator", branch: "legislative", government_level: "federal" },
  { name: "Chuck Grassley", bioguide_id: "G000386", state: "IA", party: "R", subject_type: "senator", branch: "legislative", government_level: "federal" },
  { name: "Lindsey Graham", bioguide_id: "G000359", state: "SC", party: "R", subject_type: "senator", branch: "legislative", government_level: "federal" },
  { name: "Bob Menendez", bioguide_id: "M000639", state: "NJ", party: "D", subject_type: "senator", branch: "legislative", government_level: "federal" },
  { name: "Ted Cruz", bioguide_id: "C001098", state: "TX", party: "R", subject_type: "senator", branch: "legislative", government_level: "federal" },
  { name: "Tom Cotton", bioguide_id: "C001095", state: "AR", party: "R", subject_type: "senator", branch: "legislative", government_level: "federal" },
  { name: "Joni Ernst", bioguide_id: "E000295", state: "IA", party: "R", subject_type: "senator", branch: "legislative", government_level: "federal" },
  { name: "Mike Crapo", bioguide_id: "C000880", state: "ID", party: "R", subject_type: "senator", branch: "legislative", government_level: "federal" },
  { name: "Bernie Sanders", bioguide_id: "S000033", state: "VT", party: "I", subject_type: "senator", branch: "legislative", government_level: "federal" },
  { name: "Elizabeth Warren", bioguide_id: "W000817", state: "MA", party: "D", subject_type: "senator", branch: "legislative", government_level: "federal" },
  { name: "Ron Wyden", bioguide_id: "W000779", state: "OR", party: "D", subject_type: "senator", branch: "legislative", government_level: "federal" },
  { name: "Tammy Duckworth", bioguide_id: "D000622", state: "IL", party: "D", subject_type: "senator", branch: "legislative", government_level: "federal" },
  { name: "Jon Tester", bioguide_id: "T000464", state: "MT", party: "D", subject_type: "senator", branch: "legislative", government_level: "federal" },
  { name: "Sheldon Whitehouse", bioguide_id: "W000802", state: "RI", party: "D", subject_type: "senator", branch: "legislative", government_level: "federal" },
  { name: "John Cornyn", bioguide_id: "C001056", state: "TX", party: "R", subject_type: "senator", branch: "legislative", government_level: "federal" },
  { name: "Marco Rubio", bioguide_id: "R000595", state: "FL", party: "R", subject_type: "senator", branch: "legislative", government_level: "federal" },
  { name: "Amy Klobuchar", bioguide_id: "K000367", state: "MN", party: "D", subject_type: "senator", branch: "legislative", government_level: "federal" },
  { name: "Lisa Murkowski", bioguide_id: "M001153", state: "AK", party: "R", subject_type: "senator", branch: "legislative", government_level: "federal" },
  { name: "Dan Sullivan", bioguide_id: "S001198", state: "AK", party: "R", subject_type: "senator", branch: "legislative", government_level: "federal" },
  { name: "Maria Cantwell", bioguide_id: "C000127", state: "WA", party: "D", subject_type: "senator", branch: "legislative", government_level: "federal" },
  /* Illustrative non-senate rows — open a case in Open Case to populate real IDs */
  { name: "Sample district judge", bioguide_id: "", state: "DC", party: "—", subject_type: "federal_judge_district", branch: "judicial", government_level: "federal", case_id: null },
  {
    name: "Sample mayor",
    bioguide_id: "",
    state: "IN",
    party: "—",
    subject_type: "mayor",
    branch: "executive",
    government_level: "local",
    jurisdiction: "Indianapolis, IN",
    case_id: null,
  },
  { name: "Sample state AG", bioguide_id: "", state: "—", party: "—", subject_type: "state_attorney_general", branch: "executive", government_level: "state", case_id: null },
];
