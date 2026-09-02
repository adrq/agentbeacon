// Ownership of a flag shared by runs that can supersede one another.

export interface RunToken {
  /** Start a run and take ownership. */
  begin(): number;
  /** Whether the run holding `mine` is still the current one. */
  owns(mine: number): boolean;
}

export function createRunToken(): RunToken {
  let current = 0;
  return {
    begin() {
      current += 1;
      return current;
    },
    owns(mine: number) {
      return mine === current;
    },
  };
}
