import { Component, type ReactNode } from "react";

/** Keeps one broken screen from blanking the whole app. */
export default class ErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="panel" role="alert">
        <h2>Something went wrong on this screen</h2>
        <p>
          Your inputs are saved. Reload the page to continue. If it keeps happening, the details below help us fix it.
        </p>
        <pre className="err-detail">{String(this.state.error.message || this.state.error)}</pre>
        <div className="cta-row">
          <button className="primary" onClick={() => window.location.reload()}>Reload</button>
          <button onClick={() => this.setState({ error: null })}>Try again</button>
        </div>
      </div>
    );
  }
}
