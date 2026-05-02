#pragma once

#include <optional>
#include <string>
#include <unordered_map>
#include <utility>
#include <variant>
#include <vector>

using namespace std;

using Scalar = variant<nullptr_t, bool, int64_t, double, string>;

struct Value;
struct Frame;
struct Signal;
struct Program;
struct VM;

using List = vector<Value>;
using Object = unordered_map<string, Value>;

struct Value {
  using Storage = variant<Scalar, List, Object>;

  Storage storage {};

  Value() = default;
  explicit Value(Storage data);
};

struct Signal {
  enum class Kind {
    none,
    error,
    returning,
  };

  Kind kind {Kind::none};
  optional<Value> payload {};
  string message {};

  Signal() = default;
  Signal(Kind signal_kind, optional<Value> signal_payload, string signal_message = {});
};

struct Frame {
  string name {};
  Object locals {};
};

struct Program {
  Object globals {};
  Object nodes {};
};

struct VM {
  enum class Status {
    ready,
    running,
    finished,
    failed,
  };

  vector<Frame> frames {};
  optional<Value> result {};
  Signal signal {};
  Status status {Status::ready};
};
