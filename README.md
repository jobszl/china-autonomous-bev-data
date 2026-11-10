# Source data for automated electric mobility in China

Supporting data for *Onboard equipment energy use and travel demand growth constrain electricity savings from automated electric mobility in China*. The model covers charging electricity and electricity-related CO₂ emissions across seven Chinese power-grid regions from 2024 to 2035.

`inputs/` contains model settings, source references, annual and regional outputs for 48 scenarios, and sensitivity analyses. `results/` contains reference results and archived numerical checks. From the repository root, run `python reconstruct_audit.py` with Python 3 to repeat the numerical reconstruction. `calculate_robustness.py` is the archived production script.

## Figure data

The original export names are retained for traceability. In the final manuscript the joint thresholds appear in Figure 4 and the regional results in Figure 5. Identical data files are stored once.

| Original export | Repository path |
|---|---|
| Figure2_All48_2035_Heatmap.csv | `figure-data/Figure2_All48_2035_Heatmap.csv` |
| Figure2_All48_Annual_Paired.csv | `inputs/48组情景_年度结果.csv` |
| Figure3_Deployment_Decomposition.csv | `figure-data/Figure3_Deployment_Decomposition.csv` |
| Figure3_Device_Loads_Full_Response.csv | `figure-data/Figure3_Device_Loads_Full_Response.csv` |
| Figure3_Waterfall.csv | `figure-data/Figure3_Waterfall.csv` |
| Figure4_Regional_Three_Demand_Scenarios.csv | `figure-data/Figure4_Regional_Three_Demand_Scenarios.csv` |
| Figure5_Complete_Joint_Sensitivity_Grid.csv | `inputs/联合敏感性_2035.csv` |
| Figure5_Nine_Settings_Thresholds.csv | `figure-data/Figure5_Nine_Settings_Thresholds.csv` |
| future4_reference_2035.csv | `results/future4_reference_2035.csv` |
