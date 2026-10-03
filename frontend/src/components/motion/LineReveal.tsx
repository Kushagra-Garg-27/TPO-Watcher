import { motion, useReducedMotion } from 'framer-motion'

interface LineRevealProps {
  lines: string[]
  className?: string
  lineClassName?: string
  delay?: number
  stagger?: number
  as?: 'h1' | 'h2' | 'h3' | 'p' | 'div'
}

export function LineReveal({
  lines,
  className = '',
  lineClassName = '',
  delay = 0.2,
  stagger = 0.08,
  as: Component = 'div',
}: LineRevealProps) {
  const shouldReduceMotion = useReducedMotion()

  if (shouldReduceMotion) {
    return (
      <Component className={className}>
        {lines.map((line, i) => (
          <span key={i} className={`block ${lineClassName}`}>
            {line}
          </span>
        ))}
      </Component>
    )
  }

  const containerVariants = {
    hidden: {},
    visible: {
      transition: {
        staggerChildren: stagger,
        delayChildren: delay,
      },
    },
  }

  const lineVariants = {
    hidden: {
      opacity: 0,
      y: '100%',
    },
    visible: {
      opacity: 1,
      y: '0%',
      transition: {
        duration: 0.75,
        ease: [0.16, 1, 0.3, 1] as const,
      },
    },
  }

  return (
    <Component className={className}>
      <motion.span
        className="block"
        variants={containerVariants}
        initial="hidden"
        whileInView="visible"
        viewport={{ once: true, amount: 0.2 }}
      >
        {lines.map((line, index) => (
          <span key={index} className="block overflow-hidden">
            <motion.span variants={lineVariants} className={`block ${lineClassName}`}>
              {line}
            </motion.span>
          </span>
        ))}
      </motion.span>
    </Component>
  )
}
