(module
  (func $add (param $a i32) (param $b i32) (result i32)
    local.get $a
    local.get $c
    i32.add)
  (export "add" (func $add)))
