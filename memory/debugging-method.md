# Debugging method

Two techniques earned their keep on day one. Both generalise, and both belong
in the report — **describing a debugging method is the most interview-legible
skill in the project.**

## 1. Walk the causal chain

A circuit is a chain of causes. Measure at every link and find the **first**
point where reality stops matching expectation. Everything upstream of it is
fine; everything downstream is innocent until that link is fixed.

Located a missing base resistor in two measurements.

## 2. Split the system in half

**When a chain of individually-correct readings still ends in nothing
happening, stop testing links and test the assumption at the end of the chain.**

Touching the motor's two leads directly to a supply found the wrong-wires fault
in ten seconds — after an evening of correct measurements had pointed nowhere.
The readings were all correct because the fault was *past* the end of the chain
being measured: the encoder wires were in the H-bridge outputs.

It should have been done much earlier. That is the lesson, not the fix.

## The standing rule

State what evidence would **falsify** the hypothesis before running the test.
"I think it's the driver" is not a hypothesis; "if it's the driver, AO1 will
still be at 0 V while AIN1 is high" is.

Related: [[n20-motors]], [[logic-analyser]]
