import { useCallback, useEffect, useState } from "react";
import { api, errorMessage } from "./api";

export type ApiState<T> = {
  data: T | null;
  error: string | null;
  loading: boolean;
  reload: () => void;
};

/** One GET for a panel. A manual reload is the only refetch; these panels are read-only. */
export function useApi<T>(path: string): ApiState<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [version, setVersion] = useState(0);
  const [settled, setSettled] = useState<string | null>(null);
  const requestKey = `${path}|${version}`;

  const reload = useCallback(() => setVersion((value) => value + 1), []);

  useEffect(() => {
    let active = true;
    api<T>(path)
      .then((result) => {
        if (!active) return;
        setData(result);
        setError(null);
      })
      .catch((caught: unknown) => {
        if (!active) return;
        setError(errorMessage(caught));
      })
      .finally(() => {
        if (active) setSettled(requestKey);
      });
    return () => {
      active = false;
    };
  }, [path, requestKey]);

  // A path or version change makes the current key stale, so loading turns on without a
  // setState at the top of the effect.
  return { data, error, loading: settled !== requestKey, reload };
}
