File processing
===============

.. graphviz::
    
    digraph process {
        newrank=false
        subgraph cluster_input_data {
        label=<<i>input data</i>>
        peripheries=0
        "RAW/{name}.fits" [shape=none]
        "DACE_TABLE/Dace_extracted_table.csv" [shape=none, label=<DACE_TABLE/Dace_extracted_table.csv<br/><font color="#444444">rassine.imports.types.DACE</font>>, href="../_autosummary/rassine.imports.types.DACE.html#rassine.imports.types.DACE", target="_top"]
        }

        subgraph cluster_data0 {
        peripheries=0
        "PREPROCESSED/{name}.p (bis)" [shape="none", label="PREPROCESSED/{name}.p"]

        "individual_reinterpolated.csv" [shape="none"]
        }
        subgraph cluster_data1        {
        peripheries=0
        "stacked_basic.csv" [shape="none"]
        "STACKED/{name}.p" [shape="none"]
        "MASTER/Master_spectrum_{tag}.p" [shape=none]
        }
        subgraph cluster_data2         {
        peripheries=0
        "MASTER/RASSINE_Master_spectrum_{tag}.p" [shape=none]
        "STACKED/RASSINE_{name}.p" [shape=none]
        }

        subgraph cluster_output_data {
        label=<<i>output data</i>>
        peripheries=0
        "STACKED/RASSINE_{name}.p (ter)" [shape=none,label="STACKED/RASSINE_{name}.p"]
        }
        subgraph cluster_import {
            label=<<i>import</i>>
            "preprocess_table" [shape=box, href="../cli/preprocess_table.html", target="_top"]
                "RAW/{name}.fits" -> "preprocess_table" [style="dashed"]
                "DACE_TABLE/Dace_extracted_table.csv" -> "preprocess_table"
                "preprocess_table" -> "individual_basic.csv"

            "preprocess_import" [shape=box, href="../cli/preprocess_import.html", target="_top"]
            "individual_basic.csv" -> "preprocess_import"
            "RAW/{name}.fits" -> "preprocess_import"

            "individual_basic.csv" [shape=none, label=<individual_basic.csv<br/><font color="#444444">rassine.imports.types.IndividualBasicRow</font>>, href="../_autosummary/rassine.imports.types.IndividualBasicRow.html", target="_top"]

            "preprocess_import" -> "PREPROCESSED/{name}.p"
            "preprocess_import" -> "individual_imported.csv"

            "PREPROCESSED/{name}.p" [shape=none]

            "individual_imported.csv" [shape=none]

            "individual_imported.csv" -> "reinterpolate"
            "PREPROCESSED/{name}.p" -> "reinterpolate"

            "reinterpolate"  [shape=box, href="../cli/reinterpolate.html", target="_top"]
            "reinterpolate" -> "PREPROCESSED/{name}.p (bis)"
            "reinterpolate" -> "individual_reinterpolated.csv"

        }
        subgraph cluster_stacking1 {
            label=<<i>stacking</i>>
            "stacking_create_groups" [shape="box", href="../cli/stacking_create_groups.html", target="_top"]
            "individual_reinterpolated.csv" -> "stacking_create_groups"
            "stacking_create_groups" -> "individual_group.csv"

            "individual_group.csv" [shape="none"]

            "stacking_stack" [shape="box", href="../cli/stacking_stack.html", target="_top"]

            "individual_group.csv" -> "stacking_stack"
            "individual_reinterpolated.csv" -> "stacking_stack"
            "PREPROCESSED/{name}.p (bis)" -> "stacking_stack"
            "stacking_stack" -> "stacked_basic.csv"
            "stacking_stack" -> "STACKED/{name}.p"



        }
        subgraph cluster_stacking2 {
            label=<<i>stacking</i>>
            "stacking_master_spectrum" [shape="box", href="../cli/stacking_master_spectrum.html", target="_top"]
            "STACKED/{name}.p" -> "stacking_master_spectrum"
            "stacked_basic.csv" -> "stacking_master_spectrum"
            "stacking_master_spectrum" -> "MASTER/Master_spectrum_{tag}.p"
        }
        subgraph cluster_rassine {
            label=<<i>rassine</i>>
            "rassine1" [label="rassine", shape="box", href="../cli/rassine.html", target="_top"]
            "rassine2" [label="rassine", shape="box", href="../cli/rassine.html", target="_top"]

            "MASTER/Master_spectrum_{tag}.p" -> "rassine1"
            "rassine1" -> "anchor_Master_spectrum_{tag}.ini"
            "rassine1" -> "MASTER/RASSINE_Master_spectrum_{tag}.p"


            "anchor_Master_spectrum_{tag}.ini" [shape=none]

            "STACKED/{name}.p" -> "rassine2"
            "anchor_Master_spectrum_{tag}.ini" -> "rassine2"
            "stacked_basic.csv" -> "rassine2"
            "rassine2" -> "STACKED/RASSINE_{name}.p"

        }
        subgraph cluster_matching {
            label=<<i>matching</i>>
            "stacked_basic.csv" -> "matching_anchors_scan"
            "STACKED/RASSINE_{name}.p" -> "matching_anchors_scan"
            "MASTER/RASSINE_Master_spectrum_{tag}.p" -> "matching_anchors_scan" [style="dashed"]
            "matching_anchors_scan" -> "MASTER/Master_tool_{tag}.p"

            "matching_anchors_scan" [shape=box, href="../cli/matching_anchors_scan.html", target="_top"]

            "MASTER/Master_tool_{tag}.p" [shape=none]

            "matching_anchors_filter1" [label="matching_anchors_filter", shape=box, href="../cli/matching_anchors_filter.html", target="_top"]

            "stacked_basic.csv" -> "matching_anchors_filter1"
            "STACKED/RASSINE_{name}.p" -> "matching_anchors_filter1"
            "MASTER/Master_tool_{tag}.p" -> "matching_anchors_filter1"
            "matching_anchors_filter1" -> "STACKED/RASSINE_{name}.p (bis)"
            "matching_anchors_filter1" -> "matching_anchors.csv"

            "matching_anchors_filter2" [label="matching_anchors_filter", shape=box, href="../cli/matching_anchors_filter.html", target="_top"]
            "MASTER/RASSINE_Master_spectrum_{tag}.p" -> "matching_anchors_filter2"
            "MASTER/Master_tool_{tag}.p" -> "matching_anchors_filter2"
            "matching_anchors_filter2" -> "MASTER/RASSINE_Master_spectrum_{tag}.p (bis)"
            "matching_anchors_filter2" -> "matching_anchors.csv"

            "MASTER/RASSINE_Master_spectrum_{tag}.p (bis)" [shape=none, label="MASTER/RASSINE_Master_spectrum_{tag}.p"]

            "STACKED/RASSINE_{name}.p (bis)" [shape=none,label="STACKED/RASSINE_{name}.p"]
            "matching_anchors.csv" [shape=none]


            "matching_diff" [shape=box, href="../cli/matching_diff.html", target="_top"]

            "matching_diff" [shape=box]
            "MASTER/RASSINE_Master_spectrum_{tag}.p (bis)" -> "matching_diff"
            "stacked_basic.csv" -> "matching_diff"
            "STACKED/RASSINE_{name}.p (bis)" -> "matching_diff"
            "matching_diff" -> "STACKED/RASSINE_{name}.p (ter)"
        }
    }
