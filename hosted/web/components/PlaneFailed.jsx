/**
 * The one line a page shows when the control plane did not answer.
 *
 * It is deliberately not an empty state with a warning above it: a page whose read
 * failed has no data, and rendering the list, the meter or the picker underneath the
 * notice would print the empty case (or, on the home page, four zeros) beside a
 * sentence saying nothing was read. The caller renders either this or its view.
 *
 * `role="alert"` because this replaces content the reader came for, and the test id
 * is what the browser suite asserts against a plane that answers 500 and against a
 * port nothing listens on.
 */
export default function PlaneFailed({ error }) {
  return (
    <p className="notice notice--error" role="alert" data-testid="plane-failed">
      {error}
    </p>
  );
}