# V21.5 deliberately retains the proven V21.4 deterministic extraction/firewall.
# Run the complete known-regression suite unchanged so architecture speed changes
# cannot relax UMF/Fruit-Penetrometer/Qty/spec/GST/total/two-price safeguards.
import runpy
runpy.run_path('RUN_V21_4_ACCURACY_REGRESSION_TEST.py', run_name='__main__')
