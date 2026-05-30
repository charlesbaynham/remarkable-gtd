// Sample task data — mirrors generator/tasks.example.json
// Realistic academic-PI workload (~50 items across buckets).
const DATA = {
  inbox: [
    {
      id: "IN-01",
      act: "Reply to reviewer 2 about the variance estimator — they want a proof sketch",
    },
    {
      id: "IN-02",
      act: "Sarah asked for a reference letter, deadline unclear — chase her for the date",
    },
    {
      id: "IN-03",
      act: "Book flights + accommodation for NeurIPS workshop in December",
    },
    {
      id: "IN-04",
      act: "Idea: extend the continuum model to the multi-agent case — write up half a page",
    },
    {
      id: "IN-05",
      act: "Department wants my teaching preferences for next year by some point",
    },
    { id: "IN-06", act: "Renew the GPU cluster allocation before it lapses" },
    {
      id: "IN-07",
      act: "Coffee chat with the new postdoc candidate — schedule it",
    },
  ],
  next: [
    {
      id: "NA-01",
      pri: 7,
      act: "Give Oliver final copy of EPSRC proposal",
      proj: "epsrc",
      due: "30 May",
    },
    {
      id: "NA-02",
      pri: 7,
      act: "Submit placeholder EOIs for both ERC Starter and URF",
      proj: "erc-starter",
      due: "2 Jun",
    },
    {
      id: "NA-03",
      pri: 6,
      act: "Draft the impact statement section and send to Priya for comments",
      proj: "epsrc",
      due: "3 Jun",
    },
    {
      id: "NA-04",
      pri: 6,
      act: "Finalise the budget spreadsheet — confirm RA salary scale with finance",
      proj: "epsrc",
      due: "4 Jun",
    },
    {
      id: "NA-05",
      pri: 5,
      act: "Reproduce Oliver's plots and package up code for the MLE paper — quick!",
      proj: "dai-continuum",
      due: "6 Jun",
    },
    {
      id: "NA-06",
      pri: 5,
      act: "Write referee report for JMLR submission #2241",
      proj: "service",
      due: "9 Jun",
    },
    {
      id: "NA-07",
      pri: 4,
      act: "Prepare slides for the Tuesday group meeting on diffusion samplers",
      proj: "teaching",
      due: "2 Jun",
    },
    {
      id: "NA-08",
      pri: 4,
      act: "Read and annotate the three candidate PhD applications",
      proj: "hiring",
      due: "10 Jun",
    },
    {
      id: "NA-09",
      pri: 3,
      act: "Update personal website with the two new preprints",
      proj: "admin",
      due: "15 Jun",
    },
    {
      id: "NA-10",
      pri: 3,
      act: "Sketch the experiment design for the ablation study",
      proj: "dai-continuum",
      due: "12 Jun",
    },
    {
      id: "NA-11",
      pri: 2,
      act: "Set up the shared Overleaf for the survey paper with collaborators",
      proj: "survey",
      due: "18 Jun",
    },
    {
      id: "NA-12",
      pri: 2,
      act: "Order the standing desk and second monitor for the new office",
      proj: "admin",
      due: "20 Jun",
    },
  ],
  delegated: [
    {
      id: "DG-01",
      pri: 7,
      act: "Run the full sweep on the cluster and report wall-clock + memory",
      proj: "dai-continuum",
      due: "1 Jun",
      to: "Oliver",
    },
    {
      id: "DG-02",
      pri: 6,
      act: "Clean and release the benchmark dataset with a datasheet",
      proj: "survey",
      due: "7 Jun",
      to: "Priya",
    },
    {
      id: "DG-03",
      pri: 5,
      act: "Collect signed consent forms from all study participants",
      proj: "user-study",
      due: "5 Jun",
      to: "Tom",
    },
    {
      id: "DG-04",
      pri: 4,
      act: "Draft the related-work section for the survey",
      proj: "survey",
      due: "14 Jun",
      to: "Mei",
    },
    {
      id: "DG-05",
      pri: 3,
      act: "Set up CI for the open-source repo and add the test matrix",
      proj: "dai-continuum",
      due: "11 Jun",
      to: "Oliver",
    },
    {
      id: "DG-06",
      pri: 3,
      act: "Chase the journal about the status of the revision",
      proj: "service",
      due: "9 Jun",
      to: "Admin office",
    },
  ],
  tickler: {
    week: [
      {
        id: "TK-01",
        act: "Check whether the conference released the camera-ready instructions",
      },
      { id: "TK-02", act: "Follow up with Tom if consent forms still not in" },
      {
        id: "TK-03",
        act: "Revisit the multi-agent idea once the deadline passes",
      },
    ],
    month: [
      {
        id: "TK-04",
        act: "Start thinking about the summer internship hosting logistics",
      },
      {
        id: "TK-05",
        act: "Review whether the GPU allocation is being used efficiently",
      },
      {
        id: "TK-06",
        act: "Decide on sabbatical timing and mention to the head of department",
      },
    ],
    quarter: [
      {
        id: "TK-07",
        act: "Reassess the survey paper scope — is it still timely?",
      },
      { id: "TK-08", act: "Plan the next round of PhD recruitment" },
    ],
  },
};
