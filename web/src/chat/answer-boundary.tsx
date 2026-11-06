import { Component, type ReactNode } from "react";

type Props = { children: ReactNode; fallback: ReactNode; resetKey: unknown };
type State = { failed: boolean; resetKey: unknown };

/** A bad historical widget must not take down its conversation or the composer. */
export class AnswerBoundary extends Component<Props, State> {
  state: State = { failed: false, resetKey: this.props.resetKey };

  static getDerivedStateFromError(): Partial<State> {
    return { failed: true };
  }

  static getDerivedStateFromProps(props: Props, state: State): Partial<State> | null {
    return props.resetKey !== state.resetKey ? { failed: false, resetKey: props.resetKey } : null;
  }

  render() {
    return this.state.failed ? this.props.fallback : this.props.children;
  }
}
