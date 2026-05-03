# C++ Invocation Graph

This document describes the current execution-facing native C++ call graph.

Notes:

- The graph covers the evaluator and directly used runtime helpers.
- Anything in or through `src/ryml_interface.cpp` is intentionally excluded.
- Compiler-defaulted special members are omitted.
- `src/utils.cpp` is not shown because it currently defines no functions.

```mermaid
flowchart TD
  classDef entry fill:#447,stroke:#1f6feb,stroke-width:2px;
  classDef leaf fill:#475,stroke:#2da44e,stroke-width:2px;

  h_main["src/helix.cpp::main<br/>run program and print final YAML"]
  h_run_main["src/helix.cpp::run_main<br/>execute top-level main and attach state"]
  h_make_state["src/helix.cpp::make_finished_state<br/>construct finished VM state map"]
  h_set_field["src/helix.cpp::set_map_field<br/>write map field and attach parent"]
  h_attach_parent["src/helix.cpp::attach_parent_if_missing<br/>attach structural parent once"]

  h_eval_cell["src/helix.cpp::evaluate_cell<br/>evaluate one cell recursively"]
  h_eval_resolved["src/helix.cpp::evaluate_resolved_cell<br/>follow resolved references"]
  h_eval_form["src/helix.cpp::evaluate_form<br/>evaluate actor and call callable form"]
  h_eval_arg["src/helix.cpp::evaluate_argument<br/>evaluate child and propagate errors"]
  h_render_show["src/helix.cpp::render_show_output<br/>render show result text"]

  h_lookup_context["src/helix.cpp::lookup_name_from_context<br/>search local then parent scopes"]
  h_enclosing_map["src/helix.cpp::enclosing_map<br/>walk upward to enclosing map"]
  h_lookup_name_map["src/helix.cpp::lookup_name_in_map<br/>exact then dotted lookup in one map"]
  h_lookup_dotted["src/helix.cpp::lookup_dotted_name_from<br/>walk dotted path recursively"]
  h_lookup_child["src/helix.cpp::lookup_map_child<br/>lookup one map field"]

  h_is_null["src/helix.cpp::is_null_cell<br/>treat string null as null main"]

  b_init["src/builtins.cpp::initialize_builtins<br/>install evaluator callbacks"]
  b_make_zygote["src/builtins.cpp::make_zygote<br/>construct global builtin root"]
  b_install["src/builtins.cpp::install_builtin<br/>insert FunCell builtin into zygote"]
  b_show["src/builtins.cpp::builtin_show<br/>evaluate and print one argument"]
  b_add["src/builtins.cpp::builtin_add<br/>evaluate and sum two integers"]
  b_set["src/builtins.cpp::builtin_set<br/>evaluate and assign top-level binding"]
  b_eval_arg["src/builtins.cpp::evaluate_argument<br/>delegate nested builtin argument evaluation"]
  b_expect_vm["src/builtins.cpp::expect_root_vm<br/>require current VM root map"]
  b_make_error["src/builtins.cpp::make_error<br/>construct builtin error cell"]

  c_map_ctor["src/core.cpp::MapCell::MapCell()<br/>construct empty map cell"]
  c_vec_ctor["src/core.cpp::VecCell::VecCell()<br/>construct empty vector cell"]
  c_int_ctor["src/core.cpp::IntCell::IntCell<br/>construct integer cell"]
  c_str_ctor["src/core.cpp::StrCell::StrCell(value)<br/>construct string cell"]
  c_err_ctor["src/core.cpp::ErrCell::ErrCell<br/>construct error signal cell"]
  c_make_error["src/core.cpp::make_error_cell<br/>construct shared ErrCell"]
  c_is_error["src/core.cpp::is_error_cell<br/>recognize ErrCell"]
  c_expect_arity["src/core.cpp::expect_form_arity<br/>check shared form arity"]
  c_expect_int["src/core.cpp::expect_int_cell<br/>require shared integer operand"]
  c_signal_check["src/core.cpp::SigCell::is_signal<br/>report signal kind"]
  c_fun_ctor["src/core.cpp::FunCell::FunCell<br/>construct callable builtin cell"]
  c_fun_call["src/core.cpp::FunCell::call<br/>invoke builtin implementation"]
  c_clear_desc["src/core.cpp::Cell::clear_descendant_parent_links<br/>clear descendant parent links polymorphically"]
  c_clear_map["src/core.cpp::MapCell::clear_descendant_parent_links<br/>clear map child parent links"]
  c_clear_vec["src/core.cpp::VecCell::clear_descendant_parent_links<br/>clear vector child parent links"]
  c_clear_sig["src/core.cpp::SigCell::clear_descendant_parent_links<br/>clear signal payload parent link"]

  h_main --> b_init
  h_main --> b_make_zygote
  h_main --> h_run_main
  h_main --> c_clear_desc

  h_run_main --> h_is_null
  h_run_main --> h_eval_cell
  h_run_main --> h_make_state
  h_run_main --> h_set_field
  h_run_main --> c_make_error

  h_make_state --> c_map_ctor
  h_make_state --> h_set_field
  h_make_state --> c_str_ctor
  h_make_state --> c_vec_ctor

  h_set_field --> h_attach_parent

  h_eval_cell --> h_eval_form
  h_eval_cell --> h_lookup_context
  h_eval_cell --> h_eval_resolved

  h_eval_resolved --> h_eval_cell

  h_eval_form --> h_eval_arg
  h_eval_form --> c_is_error
  h_eval_form --> c_make_error
  h_eval_form --> c_fun_call

  h_eval_arg --> h_eval_cell
  h_eval_arg --> c_is_error

  h_lookup_context --> h_enclosing_map
  h_lookup_context --> h_lookup_name_map

  h_lookup_name_map --> h_lookup_child
  h_lookup_name_map --> h_lookup_dotted
  h_lookup_dotted --> h_lookup_child
  h_lookup_dotted --> h_lookup_dotted

  h_render_show --> c_signal_check
  c_clear_map --> c_clear_desc
  c_clear_vec --> c_clear_desc
  c_clear_sig --> c_clear_desc

  b_make_zygote --> c_map_ctor
  b_make_zygote --> b_install
  b_install --> c_fun_ctor
  b_install --> h_set_field

  c_fun_call --> b_show
  c_fun_call --> b_add
  c_fun_call --> b_set

  b_show --> b_expect_vm
  b_show --> c_expect_arity
  b_show --> b_eval_arg
  b_show --> c_is_error
  b_show --> h_render_show

  b_add --> b_expect_vm
  b_add --> c_expect_arity
  b_add --> b_eval_arg
  b_add --> c_expect_int
  b_add --> c_is_error
  b_add --> c_int_ctor

  b_set --> b_expect_vm
  b_set --> c_expect_arity
  b_set --> b_eval_arg
  b_set --> c_is_error
  b_set --> b_make_error
  b_set --> h_set_field

  b_eval_arg --> h_eval_cell
  b_eval_arg --> c_is_error

  b_make_error --> c_make_error
  b_make_error --> c_err_ctor

  class h_main entry;
  class h_attach_parent,h_is_null,h_lookup_child,h_enclosing_map,b_init,b_expect_vm,c_map_ctor,c_vec_ctor,c_int_ctor,c_str_ctor,c_err_ctor,c_make_error,c_is_error,c_expect_arity,c_expect_int,c_signal_check,c_fun_ctor leaf;
```
