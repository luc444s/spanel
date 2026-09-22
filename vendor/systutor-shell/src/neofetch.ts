export const SYSTUTOR_NEOFETCH = `
           ===============++
          ===============++++++
         ===============++++++++
       ===============++++++++++++
      ==============+++++++++++++++
       ============++++++++++++++++++
         =========     ++++++++++++++++
           =====         +++++++++++++++
             ==            ++++++++++++++
            ****         ################
          *******      ################
        ***********  #################
      **************################
      #**************#############
        ***************##########
         ***************#######
           ***************####
              #*#**####***
  ────────────────────────────────────────────
  Sistema:      SYSTUTOR OSS v0.1
  Kernel:       SYSTUTOR Kernel 1.0
  Shell:        Consola Operativa (Monaco)
  Frontend:     React + TypeScript + Tailwind
  DB:           PostgreSQL 16
  Cache:        Redis
  Plugins:      logistics, crm, productos, ventas
  Ambiente:     ${typeof window !== "undefined" ? window.location.hostname : "Termux"}
  Monitor:      ${typeof navigator !== "undefined" ? navigator.platform : "aarch64"}
  ────────────────────────────────────────────
  Tema:         oscuro · monoespaciado
  Licencia:     OSS · MIT
  Url:          https://systutor.com
  ────────────────────────────────────────────
`;

export function isNeofetchCommand(command: string): boolean {
  const cmd = command.trim().toLowerCase();
  return cmd === "neofetch" || cmd === "sysinfo" || cmd === "fastfetch";
}
