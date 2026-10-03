import { motion, useReducedMotion } from 'framer-motion'

interface LetterRevealProps {
  children: string
  className?: string
  delay?: number
  stagger?: number
  as?: 'h1' | 'h2' | 'h3' | 'span' | 'div'
}

export function LetterReveal({
  children,
  className = '',
  delay = 0.1,
  stagger = 0.045,
  as: Component = 'div',
}: LetterRevealProps) {
  const shouldReduceMotion = useReducedMotion()

  if (shouldReduceMotion) {
    return <Component className={className}>{children}</Component>
  }

  const letters = Array.from(children)

  const containerVariants = {
    hidden: {},
    visible: {
      transition: {
        staggerChildren: stagger,
        delayChildren: delay,
      },
    },
  }

  const letterVariants = {
    hidden: {
      opacity: 0,
      y: '100%',
    },
    visible: {
      opacity: 1,
      y: '0%',
      transition: {
        duration: 0.85,
        ease: [0.16, 1, 0.3, 1] as const,
      },
    },
  }

  return (
    <Component className={`overflow-hidden inline-flex flex-wrap ${className}`}>
      <motion.span
        className="inline-flex flex-wrap"
        variants={containerVariants}
        initial="hidden"
        animate="visible"
      >
        {letters.map((char, index) => (
          <span key={index} className="overflow-hidden inline-block leading-none">
            <motion.span
              variants={letterVariants}
              className="inline-block"
              style={{ whiteSpace: char === ' ' ? 'pre' : 'normal' }}
            >
              {char}
            </motion.span>
          </span>
        ))}
      </motion.span>
    </Component>
  )
}
